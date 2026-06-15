"""
Main test runner - now supports both Mock and Real LLM.
"""

import sys
from pathlib import Path
from typing import Dict, Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from personalization.engine import PersonalizationEngine
from personalization.models import UserProfile, SessionState
from personalization.storage import InMemoryStorage, ProfileStorage
from personalization.extractor import PreferenceExtractor
from personalization.tests.test_scenarios import TestScenarios
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

class PersonalizationTester:
    """
    Comprehensive test runner.
    NOW supports both mock and real LLM testing.
    """
    
    def __init__(self):
        self.storage = ProfileStorage(
            spark_session=spark,
            table_name="default.user_profiles"
        )

        # Creating storage table if it does not exist already
        self.storage.create_table_if_not_exists()
        print("\n" + "=" * 80)
        print("USING REAL DATABRICKS LLM (Foundation Model)")
        print("=" * 80)

        from personalization.tests.databricks_config import create_databricks_client

        llm_client = create_databricks_client()

        self.extractor = PreferenceExtractor(
            llm_client,
            model_name="databricks-meta-llama-3-1-8b-instruct"
        )

        print("✓ Databricks LLM connected successfully\n")

        self.engine = PersonalizationEngine(self.extractor)
    
    def print_header(self, text: str, char: str = "="):
        """Print formatted header."""
        print(f"\n{char * 80}")
        print(f"{text.center(80)}")
        print(f"{char * 80}")
    
    def print_profile_state(self, profile: UserProfile, title: str = "Profile State"):
        """Print current profile state in readable format."""
        print(f"\n📊 {title}:")
        print(f"  User ID: {profile.user_id}")
        print(f"  Last Updated: {profile.updated_at}")
        
        # Colors
        colors = profile.preferences["colors"].items
        if colors:
            print(f"\n  🎨 Colors:")
            for color, pref in sorted(colors.items(), key=lambda x: x[1].weight, reverse=True):
                print(f"    - {color}: weight={pref.weight:.2f}, count={pref.count}, explicit={pref.explicit}")
        
        # Brands
        brands = profile.preferences["brands"].items
        if brands:
            print(f"\n  🏷️  Brands:")
            for brand, pref in sorted(brands.items(), key=lambda x: x[1].weight, reverse=True):
                print(f"    - {brand}: weight={pref.weight:.2f}, count={pref.count}, explicit={pref.explicit}")
        
        # Materials
        materials = profile.preferences["materials"].items
        if materials:
            print(f"\n  🧵 Materials:")
            for material, pref in sorted(materials.items(), key=lambda x: x[1].weight, reverse=True):
                print(f"    - {material}: weight={pref.weight:.2f}, count={pref.count}, explicit={pref.explicit}")
        
        # Bag Types
        bag_types = profile.preferences["bag_types"].items
        if bag_types:
            print(f"\n  👜 Bag Types:")
            for bag_type, pref in sorted(bag_types.items(), key=lambda x: x[1].weight, reverse=True):
                print(f"    - {bag_type}: weight={pref.weight:.2f}, count={pref.count}, explicit={pref.explicit}")
        
        # Attributes
        attributes = profile.preferences["attributes"].items
        if attributes:
            print(f"\n  ✨ Attributes:")
            for attr, pref in sorted(attributes.items(), key=lambda x: x[1].weight, reverse=True):
                print(f"    - {attr}: weight={pref.weight:.2f}, count={pref.count}, explicit={pref.explicit}")
        
        # Price Range
        if profile.price_range.get("min") or profile.price_range.get("max"):
            print(f"\n  💰 Price Range:")
            print(f"    Min: ₹{profile.price_range.get('min', 'N/A')}")
            print(f"    Max: ₹{profile.price_range.get('max', 'N/A')}")
            print(f"    Confidence: {profile.price_range.get('confidence', 0):.2f}")
        
        # Summary
        summary = profile.get_summary()
        print(f"\n  📝 Summary: {summary}")
    
    def print_session_state(self, session: SessionState):
        """Print current session state."""
        print(f"\n🔄 Session State:")
        print(f"  Session ID: {session.session_id}")
        print(f"  Turn Count: {session.turn_count}")
        print(f"  Is Gift: {session.is_gift}")
        print(f"  Detected Contradiction: {session.detected_contradiction}")
        
        if session.explicit_constraints:
            print(f"  Explicit Constraints: {session.explicit_constraints}")
        
        if session.temporary_interests:
            print(f"  Temporary Interests: {session.temporary_interests}")
    
    def print_context(self, context: dict):
        """Print personalization context."""
        print(f"\n💡 Personalization Context:")
        print(f"  Profile Summary: {context['profile_summary']}")
        print(f"  Is Gift Context: {context['is_gift_context']}")
        print(f"  Needs Clarification: {context['needs_clarification']}")
        
        if context['needs_clarification']:
            print(f"  ⚠️  Clarification Message: {context['clarification_message']}")
        
        if context['search_filters']:
            print(f"\n  🔍 Search Filters:")
            for key, value in context['search_filters'].items():
                print(f"    {key}: {value}")
    
    def _print_extraction(self, extraction: dict):
        """Print raw extraction details safely."""
        print(f"\n🔍 Raw Extraction:")
        print(f"  Intent Type: {extraction.get('intent_type', 'N/A')}")
        print(f"  Confidence: {extraction.get('signals', {}).get('confidence', 0.0)}")
        
        colors = extraction.get('extracted', {}).get('colors', [])
        if colors:
            print(f"  ✅ Extracted Colors: {colors}")
        
        neg_colors = extraction.get('negations', {}).get('colors', [])
        if neg_colors:
            print(f"  ❌ Negated Colors: {neg_colors}")
        
        materials = extraction.get('extracted', {}).get('materials', [])
        if materials:
            print(f"  ✅ Extracted Materials: {materials}")
        
        neg_materials = extraction.get('negations', {}).get('materials', [])
        if neg_materials:
            print(f"  ❌ Negated Materials: {neg_materials}")
        
        bag_types = extraction.get('extracted', {}).get('bag_types', [])
        if bag_types:
            print(f"  ✅ Bag Types: {bag_types}")
        
        attributes = extraction.get('extracted', {}).get('attributes', [])
        if attributes:
            print(f"  ✅ Attributes: {attributes}")
        
        if extraction.get('signals', {}).get('is_gift', False):
            print(f"  🎁 Gift Context: True")
    
    def run_scenario(self, scenario_name: str, messages: list, description: str, user_id: str = "test_user"):
        """Run a single test scenario."""
        
        self.print_header(f"SCENARIO: {scenario_name}", "=")
        print(f"\n📖 Description: {description}")
        
        profile = self.storage.load_profile(user_id)
        session = None
        
        for i, message in enumerate(messages, 1):
            print(f"\n{'─' * 80}")
            print(f"💬 Turn {i}: User says: \"{message}\"")
            print(f"{'─' * 80}")
            
            # Show raw extraction
            extraction_raw = self.extractor.extract(message)
            extraction = extraction_raw
            self._print_extraction(extraction)

            # Process message
            profile, session, context = self.engine.process_message(
                user_id,
                message,
                profile,
                session
            )
            
            # Show results
            self.print_context(context)
        
        self.print_session_state(session)
        self.print_profile_state(profile, f"Profile After {scenario_name}")
        
        self.storage.save_profile(profile)
        
        print(f"\n{'=' * 80}")
        input("\nPress Enter to continue to next scenario...")
    
    # ... (keep other methods: run_multi_session_test, run_single_scenario_test, run_interactive_test)
    
    def run_multi_session_test(self):
        """Run comprehensive multi-session test."""
        
        self.print_header("MULTI-SESSION PERSONALIZATION TEST", "=")
        
        user_id = "multi_session_user"
        scenarios = TestScenarios.get_all_scenarios()
        
        print(f"\n🎯 Testing {len(scenarios)} scenarios across multiple sessions")
        print(f"👤 User ID: {user_id}")
        
        input("\nPress Enter to start...")
        
        for scenario_name, messages, description in scenarios:
            self.run_scenario(scenario_name, messages, description, user_id)
        
        self.print_header("FINAL PROFILE REVIEW", "=")
        final_profile = self.storage.load_profile(user_id)
        self.print_profile_state(final_profile, "Final Profile After All Sessions")
        
        print("\n" + "=" * 80)
        print("TEST COMPLETE ✓".center(80))
        print("=" * 80)
    
    def run_single_scenario_test(self, scenario_num: int = 1):
        """Run a single scenario for quick testing."""
        
        scenarios = TestScenarios.get_all_scenarios()
        
        if scenario_num < 1 or scenario_num > len(scenarios):
            print(f"❌ Invalid scenario number. Choose between 1 and {len(scenarios)}")
            return
        
        scenario_name, messages, description = scenarios[scenario_num - 1]
        
        self.print_header(f"SINGLE SCENARIO TEST", "=")
        self.run_scenario(scenario_name, messages, description, f"single_test_user_{scenario_num}")
    
    def run_interactive_test(self):
        """Interactive testing mode."""
        
        self.print_header("INTERACTIVE TEST MODE", "=")
        
        user_id = "interactive_user"
        profile = self.storage.load_profile(user_id)
        session = None
        
        print("\n💬 Enter messages to test personalization (type 'quit' to exit)")
        print("   Type 'profile' to see current profile")
        print("   Type 'session' to see current session")
        print("   Type 'reset' to reset profile")
        
        while True:
            print(f"\n{'─' * 80}")
            message = input("You: ").strip()
            
            if message.lower() == 'quit':
                print("👋 Goodbye!")
                break
            
            if message.lower() == 'profile':
                self.print_profile_state(profile)
                continue
            
            if message.lower() == 'session':
                if session:
                    self.print_session_state(session)
                else:
                    print("No active session")
                continue
            
            if message.lower() == 'reset':
                profile = UserProfile(user_id=user_id)
                session = None
                self.storage.save_profile(profile)
                print("✓ Profile reset")
                continue
            
            if not message:
                continue
            
            # Show extraction
            extraction_raw = self.extractor.extract(message)
            extraction = extraction_raw
            self._print_extraction(extraction)

            # Process message
            profile, session, context = self.engine.process_message(
                user_id,
                message,
                profile,
                session
            )
            
            # Show results
            self.print_context(context)
            
            # Save profile
            self.storage.save_profile(profile)


def main():
    """Main entry point."""
    
    print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║                  PERSONALIZATION SYSTEM TEST SUITE                           ║
║                  Standalone Testing (No Flask Required)                      ║
╚══════════════════════════════════════════════════════════════════════════════╝
    """)
    
    print("\n⚠️  Ensure Databricks credentials are configured:")
    print("   - Running inside Databricks workspace OR")
    print("   - DATABRICKS_WORKSPACE_URL")
    print("   - DATABRICKS_TOKEN")
    input("\nPress Enter to continue...")

    # Create tester
    try:
        tester = PersonalizationTester()
    except Exception as e:
        print(f"\n❌ Error initializing tester: {e}")
        return
    
    # Choose test mode
    print("\nChoose a test mode:")
    print("  1. Multi-Session Test (Comprehensive - All Scenarios)")
    print("  2. Single Scenario Test (Quick - One Scenario)")
    print("  3. Interactive Test (Manual Testing)")
    print("  4. Exit")
    
    choice = input("\nEnter choice (1-4): ").strip()
    
    if choice == "1":
        tester.run_multi_session_test()
    elif choice == "2":
        print("\nAvailable scenarios:")
        scenarios = TestScenarios.get_all_scenarios()
        for i, (name, _, desc) in enumerate(scenarios, 1):
            print(f"  {i}. {name}")
        
        scenario_num = input(f"\nChoose scenario (1-{len(scenarios)}): ").strip()
        try:
            tester.run_single_scenario_test(int(scenario_num))
        except ValueError:
            print("❌ Invalid input")
    elif choice == "3":
        tester.run_interactive_test()
    elif choice == "4":
        print("👋 Goodbye!")
    else:
        print("❌ Invalid choice")


if __name__ == "__main__":
    main()