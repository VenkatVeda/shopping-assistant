from databricks.sdk import WorkspaceClient
import json

w = WorkspaceClient()

# Check table count
result = w.statement_execution.execute_statement(
    warehouse_id='8eba55b5eb828697',
    statement='SELECT COUNT(*) FROM sandbox.venkat.user_profiles',
    wait_timeout='30s'
)

count = result.result.data_array[0][0]
print(f"✓ Total user profiles: {count}\n")

# Show recent profiles with details
result = w.statement_execution.execute_statement(
    warehouse_id='8eba55b5eb828697',
    statement='SELECT user_id, profile_data, created_at, updated_at FROM sandbox.venkat.user_profiles ORDER BY updated_at DESC LIMIT 5',
    wait_timeout='30s'
)

if result.result and result.result.data_array:
    print("Recent profiles:")
    print("-" * 80)
    for row in result.result.data_array:
        user_id = row[0]
        profile_data = json.loads(row[1])
        created = row[2]
        updated = row[3]
        
        print(f"\nUser ID: {user_id}")
        print(f"  Created: {created}")
        print(f"  Updated: {updated}")
        print(f"  Preferences:")
        if profile_data.get('preferences'):
            prefs = profile_data['preferences']
            if prefs.get('categories'):
                print(f"    Categories: {prefs['categories']}")
            if prefs.get('colors'):
                print(f"    Colors: {prefs['colors']}")
            if prefs.get('brands'):
                print(f"    Brands: {prefs['brands']}")
else:
    print("No profiles yet")


# Now check table
result = w.statement_execution.execute_statement(
    warehouse_id='8eba55b5eb828697',
    statement='SELECT COUNT(*) FROM sandbox.venkat.user_profiles',
    wait_timeout='30s'
)

print(f"Query status: {result.status.state}")
if result.result:
    print(f"User profiles count: {result.result.data_array[0][0]}")

