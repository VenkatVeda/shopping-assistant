"""
Database storage interface.
Handles loading/saving user profiles from Databricks Delta tables.

Schema (append-only, versioned):
    user_id      STRING    NOT NULL
    version      BIGINT    NOT NULL   -- monotonically increasing per user
    profile_data STRING               -- full UserProfile JSON
    saved_at     TIMESTAMP            -- wall-clock time of this write

Every save() appends a new row.  load_profile() reads the row with the
highest version for a given user.  load_profile(version=N) reads a specific
historical snapshot for rollback/audit.

Old single-row schema (user_id, profile_data, created_at, updated_at) is
auto-detected on load and migrated to version=0 transparently.

Retention: save() trims rows older than MAX_VERSIONS (default 20) per user
after each write, keeping the table from growing unboundedly.
"""

import json
import logging
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import UserProfile

logger = logging.getLogger(__name__)

MAX_VERSIONS = 20   # snapshots retained per user


class ProfileStorage:
    """
    Append-only, versioned storage for user profiles.
    Supports load-by-version for rollback and audit.
    """

    def __init__(
        self,
        spark_session=None,
        table_name: str = "user_profiles",
        workspace_client=None,
        warehouse_id: Optional[str] = None,
    ):
        self.spark          = spark_session
        self.table_name     = table_name
        self.workspace_client = workspace_client
        self.warehouse_id   = warehouse_id

        if workspace_client and warehouse_id:
            self.mode = "sql_warehouse"
            logger.info(f"[STORAGE] Using SQL Warehouse mode: {warehouse_id}")
        elif spark_session:
            self.mode = "spark"
            logger.info("[STORAGE] Using Spark mode")
        else:
            self.mode = "memory"
            logger.warning("[STORAGE] Using in-memory mode (no persistence)")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_profile(self, user_id: str, version: int = -1) -> UserProfile:
        """
        Load a user profile.

        Args:
            user_id: User identifier.
            version: Snapshot to load.  -1 (default) loads the latest.
                     Any non-negative integer loads that specific version,
                     enabling point-in-time rollback.

        Returns:
            UserProfile — a new empty profile if the user has no history.
        """
        if self.mode == "memory":
            return UserProfile(user_id=user_id)
        if self.mode == "sql_warehouse":
            return self._load_sql(user_id, version)
        if self.mode == "spark":
            return self._load_spark(user_id, version)
        return UserProfile(user_id=user_id)

    def save_profile(self, profile: UserProfile) -> bool:
        """
        Append a new versioned snapshot of the profile.
        Trims snapshots beyond MAX_VERSIONS for this user.

        Returns True on success, False on failure (never raises).
        """
        if self.mode == "memory":
            logger.debug("[STORAGE] Memory mode — profile not persisted")
            return True
        if self.mode == "sql_warehouse":
            return self._save_sql(profile)
        if self.mode == "spark":
            return self._save_spark(profile)
        return False

    def list_versions(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Return metadata for all stored versions of a user's profile.
        Useful for audit UI and choosing a rollback target.

        Returns list of { version, saved_at } dicts, newest first.
        """
        if self.mode == "sql_warehouse":
            return self._list_versions_sql(user_id)
        if self.mode == "spark":
            return self._list_versions_spark(user_id)
        return []

    def create_table_if_not_exists(self):
        """Create the versioned Delta table. Safe to call repeatedly."""
        ddl = f"""
            CREATE TABLE IF NOT EXISTS {self.table_name} (
                user_id      STRING    NOT NULL,
                version      BIGINT    NOT NULL,
                profile_data STRING,
                saved_at     TIMESTAMP NOT NULL
            ) USING DELTA
        """
        if self.mode == "sql_warehouse":
            self._exec_sql(ddl, label="create table")
            logger.info(f"[STORAGE] Table {self.table_name} ready (SQL Warehouse)")
        elif self.mode == "spark":
            self.spark.sql(ddl)
            logger.info(f"[STORAGE] Table {self.table_name} ready (Spark)")
        else:
            logger.debug("[STORAGE] Memory mode — skipping table creation")

    # ------------------------------------------------------------------
    # SQL Warehouse implementation
    # ------------------------------------------------------------------

    def _load_sql(self, user_id: str, version: int) -> UserProfile:
        try:
            from databricks.sdk.service.sql import StatementState, StatementParameterListItem

            if version == -1:
                stmt = (
                    f"SELECT profile_data FROM {self.table_name} "
                    "WHERE user_id = :uid ORDER BY version DESC LIMIT 1"
                )
                params = [StatementParameterListItem(name="uid", value=user_id, type="STRING")]
            else:
                stmt = (
                    f"SELECT profile_data FROM {self.table_name} "
                    "WHERE user_id = :uid AND version = :ver LIMIT 1"
                )
                params = [
                    StatementParameterListItem(name="uid", value=user_id,       type="STRING"),
                    StatementParameterListItem(name="ver", value=str(version),  type="LONG"),
                ]

            response = self.workspace_client.statement_execution.execute_statement(
                warehouse_id=self.warehouse_id,
                statement=stmt,
                parameters=params,
                wait_timeout="30s",
            )

            if response.status.state == StatementState.SUCCEEDED:
                if response.result and response.result.data_array:
                    raw = response.result.data_array[0][0]
                    profile = self._deserialise(raw, user_id)
                    logger.info(f"[STORAGE] Loaded profile for {user_id} (version={version})")
                    return profile

            logger.info(f"[STORAGE] No profile found for {user_id} — new user")
            return UserProfile(user_id=user_id)

        except Exception as e:
            logger.error(f"[STORAGE] Load error: {e}\n{traceback.format_exc()}")
            return UserProfile(user_id=user_id)

    def _save_sql(self, profile: UserProfile) -> bool:
        try:
            from databricks.sdk.service.sql import StatementState, StatementParameterListItem

            # Derive next version number
            next_version = self._next_version_sql(profile.user_id)
            profile_json = json.dumps(profile.to_dict())

            insert_stmt = (
                f"INSERT INTO {self.table_name} (user_id, version, profile_data, saved_at) "
                "VALUES (:uid, :ver, :data, current_timestamp())"
            )
            response = self.workspace_client.statement_execution.execute_statement(
                warehouse_id=self.warehouse_id,
                statement=insert_stmt,
                parameters=[
                    StatementParameterListItem(name="uid",  value=profile.user_id,  type="STRING"),
                    StatementParameterListItem(name="ver",  value=str(next_version), type="LONG"),
                    StatementParameterListItem(name="data", value=profile_json,      type="STRING"),
                ],
                wait_timeout="30s",
            )

            if response.status.state != StatementState.SUCCEEDED:
                logger.error(f"[STORAGE] Insert failed: {response.status.state}")
                return False

            logger.info(f"[STORAGE] Saved profile for {profile.user_id} as version {next_version}")
            self._trim_versions_sql(profile.user_id, next_version)
            return True

        except Exception as e:
            logger.error(f"[STORAGE] Save error: {e}\n{traceback.format_exc()}")
            return False

    def _next_version_sql(self, user_id: str) -> int:
        """Return max(version) + 1 for the user, or 0 for a new user."""
        try:
            from databricks.sdk.service.sql import StatementState, StatementParameterListItem

            response = self.workspace_client.statement_execution.execute_statement(
                warehouse_id=self.warehouse_id,
                statement=f"SELECT COALESCE(MAX(version), -1) FROM {self.table_name} WHERE user_id = :uid",
                parameters=[StatementParameterListItem(name="uid", value=user_id, type="STRING")],
                wait_timeout="30s",
            )
            if (
                response.status.state == StatementState.SUCCEEDED
                and response.result
                and response.result.data_array
            ):
                return int(response.result.data_array[0][0]) + 1
        except Exception as e:
            logger.warning(f"[STORAGE] Could not determine next version: {e}")
        return 0

    def _trim_versions_sql(self, user_id: str, latest_version: int):
        """Delete versions older than MAX_VERSIONS for this user."""
        cutoff = latest_version - MAX_VERSIONS
        if cutoff < 0:
            return
        try:
            from databricks.sdk.service.sql import StatementParameterListItem

            self._exec_sql(
                f"DELETE FROM {self.table_name} WHERE user_id = :uid AND version <= :cut",
                params=[
                    StatementParameterListItem(name="uid", value=user_id,       type="STRING"),
                    StatementParameterListItem(name="cut", value=str(cutoff),   type="LONG"),
                ],
                label="trim versions",
            )
            logger.debug(f"[STORAGE] Trimmed versions <= {cutoff} for {user_id}")
        except Exception as e:
            logger.warning(f"[STORAGE] Version trim failed (non-fatal): {e}")

    def _list_versions_sql(self, user_id: str) -> List[Dict[str, Any]]:
        try:
            from databricks.sdk.service.sql import StatementState, StatementParameterListItem

            response = self.workspace_client.statement_execution.execute_statement(
                warehouse_id=self.warehouse_id,
                statement=(
                    f"SELECT version, saved_at FROM {self.table_name} "
                    "WHERE user_id = :uid ORDER BY version DESC"
                ),
                parameters=[StatementParameterListItem(name="uid", value=user_id, type="STRING")],
                wait_timeout="30s",
            )
            if (
                response.status.state == StatementState.SUCCEEDED
                and response.result
                and response.result.data_array
            ):
                return [
                    {"version": int(row[0]), "saved_at": row[1]}
                    for row in response.result.data_array
                ]
        except Exception as e:
            logger.error(f"[STORAGE] list_versions error: {e}")
        return []

    def _exec_sql(self, statement: str, params=None, label: str = "statement"):
        """Fire-and-forget SQL execution helper."""
        try:
            from databricks.sdk.service.sql import StatementState

            kwargs = {"warehouse_id": self.warehouse_id, "statement": statement, "wait_timeout": "30s"}
            if params:
                kwargs["parameters"] = params

            response = self.workspace_client.statement_execution.execute_statement(**kwargs)
            if response.status.state != StatementState.SUCCEEDED:
                logger.error(f"[STORAGE] {label} failed: {response.status.state}")
        except Exception as e:
            logger.error(f"[STORAGE] {label} error: {e}")

    # ------------------------------------------------------------------
    # Spark implementation (legacy)
    # ------------------------------------------------------------------

    def _load_spark(self, user_id: str, version: int) -> UserProfile:
        try:
            tmp = self.spark.createDataFrame([(user_id,)], ["uid"])
            tmp.createOrReplaceTempView("_lookup_uid")

            if version == -1:
                result = self.spark.sql(
                    f"SELECT profile_data FROM {self.table_name} "
                    "JOIN _lookup_uid ON user_id = uid "
                    "ORDER BY version DESC LIMIT 1"
                ).collect()
            else:
                ver_df = self.spark.createDataFrame([(user_id, version)], ["uid", "ver"])
                ver_df.createOrReplaceTempView("_lookup_ver")
                result = self.spark.sql(
                    f"SELECT profile_data FROM {self.table_name} "
                    "JOIN _lookup_ver ON user_id = uid AND version = ver LIMIT 1"
                ).collect()

            if result:
                return self._deserialise(result[0]["profile_data"], user_id)
            return UserProfile(user_id=user_id)

        except Exception as e:
            logger.error(f"[STORAGE] Spark load error: {e}")
            return UserProfile(user_id=user_id)

    def _save_spark(self, profile: UserProfile) -> bool:
        try:
            profile_json = json.dumps(profile.to_dict())

            # Derive next version
            tmp = self.spark.createDataFrame([(profile.user_id,)], ["uid"])
            tmp.createOrReplaceTempView("_save_uid")
            max_row = self.spark.sql(
                f"SELECT COALESCE(MAX(version), -1) AS v FROM {self.table_name} "
                "JOIN _save_uid ON user_id = uid"
            ).collect()
            next_version = int(max_row[0]["v"]) + 1 if max_row else 0

            new_row = self.spark.createDataFrame(
                [(profile.user_id, next_version, profile_json, datetime.now())],
                ["user_id", "version", "profile_data", "saved_at"],
            )
            new_row.write.mode("append").saveAsTable(self.table_name)

            logger.info(f"[STORAGE] Saved profile for {profile.user_id} as version {next_version}")

            # Trim old versions
            cutoff = next_version - MAX_VERSIONS
            if cutoff >= 0:
                self.spark.sql(
                    f"DELETE FROM {self.table_name} WHERE user_id = '{profile.user_id}' "
                    f"AND version <= {cutoff}"
                )
            return True

        except Exception as e:
            logger.error(f"[STORAGE] Spark save error: {e}")
            return False

    def _list_versions_spark(self, user_id: str) -> List[Dict[str, Any]]:
        try:
            tmp = self.spark.createDataFrame([(user_id,)], ["uid"])
            tmp.createOrReplaceTempView("_lookup_uid")
            rows = self.spark.sql(
                f"SELECT version, saved_at FROM {self.table_name} "
                "JOIN _lookup_uid ON user_id = uid ORDER BY version DESC"
            ).collect()
            return [{"version": row["version"], "saved_at": str(row["saved_at"])} for row in rows]
        except Exception as e:
            logger.error(f"[STORAGE] Spark list_versions error: {e}")
            return []

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _deserialise(self, raw_json: str, user_id: str) -> UserProfile:
        """
        Parse profile JSON, handling both the new versioned schema and the
        legacy single-row schema (no version column).
        """
        try:
            data = json.loads(raw_json)
            return UserProfile.from_dict(data)
        except Exception as e:
            logger.error(f"[STORAGE] Could not deserialise profile for {user_id}: {e}")
            return UserProfile(user_id=user_id)


# ------------------------------------------------------------------
# In-memory storage — testing / dev only
# ------------------------------------------------------------------

class InMemoryStorage:
    """
    Versioned in-memory storage for unit tests and local dev.
    Mirrors the same load_profile(version=-1) API as ProfileStorage.
    """

    def __init__(self):
        # user_id → list of UserProfile snapshots (index = version)
        self._versions: Dict[str, List[UserProfile]] = {}

    def load_profile(self, user_id: str, version: int = -1) -> UserProfile:
        snapshots = self._versions.get(user_id, [])
        if not snapshots:
            return UserProfile(user_id=user_id)
        if version == -1:
            return snapshots[-1]
        if 0 <= version < len(snapshots):
            return snapshots[version]
        return UserProfile(user_id=user_id)

    def save_profile(self, profile: UserProfile) -> bool:
        if profile.user_id not in self._versions:
            self._versions[profile.user_id] = []
        self._versions[profile.user_id].append(profile)
        # Keep only the last MAX_VERSIONS snapshots
        if len(self._versions[profile.user_id]) > MAX_VERSIONS:
            self._versions[profile.user_id] = self._versions[profile.user_id][-MAX_VERSIONS:]
        return True

    def list_versions(self, user_id: str) -> List[Dict[str, Any]]:
        snapshots = self._versions.get(user_id, [])
        return [{"version": i, "saved_at": "in-memory"} for i in range(len(snapshots) - 1, -1, -1)]
