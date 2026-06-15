"""
Standalone logic validation tests - NO Databricks required.
Tests the core business logic without LLM/DB dependencies.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from personalization.models import UserProfile, SessionState, PreferenceCategory, Preference
from personalization.rules import PersonalizationRules
from personalization.counters import PreferenceCounter
from personalization.post_processing import fix_color_modifiers, extract_style_attributes, detect_special_occasion


def test_preference_category():
    """Test PreferenceCategory logic."""
    print("\n" + "="*80)
    print("TEST 1: PreferenceCategory Add/Update")
    print("="*80)
    
    category = PreferenceCategory()
    
    # Add new preference
    category.add_or_update("black", weight_delta=0.5, explicit=True)
    assert "black" in category.items
    assert category.items["black"].weight == 1.0  # Initial explicit = 1.0
    assert category.items["black"].explicit == True
    print("✓ Add explicit preference: black (weight=1.0)")
    
    # Update existing preference
    category.add_or_update("black", weight_delta=0.1, explicit=False)
    assert category.items["black"].count == 2
    print(f"✓ Update preference: black (count={category.items['black'].count}, weight={category.items['black'].weight:.2f})")
    
    # Add disliked item
    category.add_disliked("bright", weight=0.8)
    assert "bright" in category.disliked_items
    assert "bright" not in category.items
    print("✓ Add disliked: bright")
    
    # Get top preferences
    category.add_or_update("brown", weight_delta=0.3, explicit=False)
    category.add_or_update("blue", weight_delta=0.2, explicit=False)
    top = category.get_top(2, min_weight=0.3)
    print(f"✓ Get top 2: {top}")
    
    print("✅ TEST 1 PASSED\n")


def test_user_profile():
    """Test UserProfile creation and updates."""
    print("="*80)
    print("TEST 2: UserProfile Creation and Methods")
    print("="*80)
    
    profile = UserProfile(user_id="test_user_123")
    
    # Test basic structure
    assert profile.user_id == "test_user_123"
    assert "colors" in profile.preferences
    assert "brands" in profile.preferences
    print("✓ Profile created with correct structure")
    
    # Add preferences
    profile.preferences["colors"].add_or_update("black", 0.5, explicit=True)
    profile.preferences["brands"].add_or_update("nike", 0.3, explicit=False)
    
    # Test get_summary
    summary = profile.get_summary()
    assert "black" in summary.lower()
    print(f"✓ Profile summary: {summary}")
    
    # Test to_dict and from_dict
    profile_dict = profile.to_dict()
    assert isinstance(profile_dict, dict)
    assert profile_dict["user_id"] == "test_user_123"
    print("✓ Profile serialization to dict")
    
    profile_restored = UserProfile.from_dict(profile_dict)
    assert profile_restored.user_id == profile.user_id
    assert "black" in profile_restored.preferences["colors"].items
    print("✓ Profile deserialization from dict")
    
    print("✅ TEST 2 PASSED\n")


def test_session_state():
    """Test SessionState logic."""
    print("="*80)
    print("TEST 3: SessionState Management")
    print("="*80)
    
    session = SessionState(
        session_id="sess_123",
        user_id="user_123"
    )
    
    # Test basic properties
    assert session.session_id == "sess_123"
    assert session.is_gift == False
    assert session.turn_count == 0
    print("✓ Session created with correct defaults")
    
    # Test constraint updates
    session.explicit_constraints["colors"] = ["red", "black"]
    session.explicit_constraints["price_max"] = [5000]
    print(f"✓ Explicit constraints: {session.explicit_constraints}")
    
    # Test gift flag
    session.is_gift = True
    session.temporary_interests["colors"] = ["pink", "gold"]
    assert session.is_gift == True
    print(f"✓ Gift mode with temporary interests: {session.temporary_interests}")
    
    # Test reset
    session.reset_constraints()
    assert len(session.explicit_constraints) == 0
    print("✓ Constraints reset")
    
    print("✅ TEST 3 PASSED\n")


def test_personalization_rules():
    """Test PersonalizationRules classification logic."""
    print("="*80)
    print("TEST 4: PersonalizationRules Classification")
    print("="*80)
    
    rules = PersonalizationRules()
    profile = UserProfile(user_id="test")
    session = SessionState(session_id="s1", user_id="test")
    
    # Test 1: Explicit preference
    classification = rules.classify_update_type(
        intent_type="explicit_preference",
        extracted={"colors": ["black"], "brands": []},
        profile=profile,
        session=session,
        user_message="I like black bags"
    )
    assert classification["profile_action"] == "hard_update"
    print(f"✓ Explicit preference → {classification['profile_action']}")
    
    # Test 2: Query
    classification = rules.classify_update_type(
        intent_type="query",
        extracted={"colors": ["red"], "brands": []},
        profile=profile,
        session=session,
        user_message="Show me red bags"
    )
    assert classification["profile_action"] == "no_update"
    print(f"✓ Query → {classification['profile_action']}")
    
    # Test 3: Gift
    session.is_gift = True
    classification = rules.classify_update_type(
        intent_type="query",
        extracted={"colors": ["pink"], "brands": []},
        profile=profile,
        session=session,
        user_message="Looking for pink bag for my sister"
    )
    assert classification["profile_action"] == "no_update"
    print(f"✓ Gift context → {classification['profile_action']}")
    
    # Test 4: Negation
    session.is_gift = False
    classification = rules.classify_update_type(
        intent_type="negation",
        extracted={"colors": ["bright"], "brands": []},
        profile=profile,
        session=session,
        user_message="I don't like bright colors"
    )
    assert classification["profile_action"] == "remove_preference"
    print(f"✓ Negation → {classification['profile_action']}")
    
    # Test 5: Requirement
    classification = rules.classify_update_type(
        intent_type="requirement",
        extracted={"attributes": ["waterproof"], "brands": []},
        profile=profile,
        session=session,
        user_message="I need a waterproof bag"
    )
    assert classification["profile_action"] == "requirement_update"
    print(f"✓ Requirement → {classification['profile_action']}")
    
    print("✅ TEST 4 PASSED\n")


def test_preference_counter():
    """Test PreferenceCounter weight management."""
    print("="*80)
    print("TEST 5: PreferenceCounter Logic")
    print("="*80)
    
    counter = PreferenceCounter()
    category = PreferenceCategory()
    
    # Test explicit update
    counter.update_preference(category, "black", "explicit")
    assert "black" in category.items
    assert category.items["black"].weight >= 0.5
    print(f"✓ Explicit update: black (weight={category.items['black'].weight:.2f})")
    
    # Test inferred update
    counter.update_preference(category, "brown", "inferred")
    assert "brown" in category.items
    assert category.items["brown"].weight < 0.5
    print(f"✓ Inferred update: brown (weight={category.items['brown'].weight:.2f})")
    
    # Test negation
    counter.update_preference(category, "black", "negation")
    print(f"✓ Negation applied: black (weight={category.items['black'].weight:.2f})")
    
    print("✅ TEST 5 PASSED\n")


def test_post_processing():
    """Test post-processing functions."""
    print("="*80)
    print("TEST 6: Post-Processing Functions")
    print("="*80)
    
    # Test fix_color_modifiers
    flattened = {
        "colors": ["pink"],
        "negations": {"colors": ["bright"], "brands": [], "materials": []}
    }
    user_message = "I'm looking for a bright pink bag"
    result = fix_color_modifiers(flattened, user_message)
    
    # "bright" should be removed from negations since it's an adjective
    assert "bright" not in result["negations"]["colors"]
    print(f"✓ fix_color_modifiers: removed 'bright' from negations")
    print(f"  Colors: {result['colors']}")
    print(f"  Negations: {result['negations']['colors']}")
    
    # Test extract_style_attributes
    flattened = {"attributes": []}
    user_message = "I want a minimalist and cute bag"
    result = extract_style_attributes(flattened, user_message)
    assert "minimalist" in result["attributes"]
    assert "cute" in result["attributes"]
    print(f"✓ extract_style_attributes: {result['attributes']}")
    
    # Test detect_special_occasion
    assert detect_special_occasion("Looking for a bag for birthday") == True
    assert detect_special_occasion("I need a bag for wedding") == True
    assert detect_special_occasion("Show me office bags") == False
    print(f"✓ detect_special_occasion: working correctly")
    
    print("✅ TEST 6 PASSED\n")


def test_weight_boundaries():
    """Test that weights stay within bounds."""
    print("="*80)
    print("TEST 7: Weight Boundary Enforcement")
    print("="*80)
    
    category = PreferenceCategory()
    
    # Try to exceed max weight
    category.add_or_update("black", weight_delta=1.0, explicit=True)  # Start at 1.0
    category.add_or_update("black", weight_delta=1.0, explicit=False)  # Try to go to 2.0
    
    assert category.items["black"].weight <= 1.0
    print(f"✓ Max weight enforced: {category.items['black'].weight:.2f} <= 1.0")
    
    # Try to go below min weight
    category.add_or_update("brown", weight_delta=0.0, explicit=False)  # Start low
    category.remove_or_downweight("brown", full_remove=False)  # Try to go negative
    
    if "brown" in category.items:
        assert category.items["brown"].weight >= 0.1
        print(f"✓ Min weight enforced: {category.items['brown'].weight:.2f} >= 0.1")
    else:
        print("✓ Item removed when weight too low")
    
    print("✅ TEST 7 PASSED\n")


def test_disliked_items():
    """Test disliked items management."""
    print("="*80)
    print("TEST 8: Disliked Items Management")
    print("="*80)
    
    category = PreferenceCategory()
    
    # Add a liked item
    category.add_or_update("black", weight_delta=0.5, explicit=True)
    assert "black" in category.items
    print("✓ Added liked item: black")
    
    # Now dislike it
    category.add_disliked("black", weight=0.8)
    assert "black" not in category.items
    assert "black" in category.disliked_items
    print("✓ Moved to disliked items")
    
    # Try to like it again
    category.add_or_update("black", weight_delta=0.5, explicit=True)
    assert "black" in category.items
    assert "black" not in category.disliked_items
    print("✓ Moved back to liked items")
    
    print("✅ TEST 8 PASSED\n")


def test_profile_serialization():
    """Test complete profile save/load cycle."""
    print("="*80)
    print("TEST 9: Profile Serialization/Deserialization")
    print("="*80)
    
    # Create complex profile
    profile = UserProfile(user_id="complex_user")
    profile.preferences["colors"].add_or_update("black", 0.5, explicit=True)
    profile.preferences["colors"].add_or_update("brown", 0.2, explicit=False)
    profile.preferences["colors"].add_disliked("bright", 0.8)
    profile.preferences["brands"].add_or_update("nike", 0.6, explicit=True)
    profile.price_range = {"min": 2000, "max": 5000, "confidence": 0.9}
    
    # Serialize
    profile_dict = profile.to_dict()
    print(f"✓ Serialized profile: {len(str(profile_dict))} characters")
    
    # Deserialize
    profile_restored = UserProfile.from_dict(profile_dict)
    
    # Verify all data preserved
    assert profile_restored.user_id == "complex_user"
    assert "black" in profile_restored.preferences["colors"].items
    assert "bright" in profile_restored.preferences["colors"].disliked_items
    assert "nike" in profile_restored.preferences["brands"].items
    assert profile_restored.price_range["min"] == 2000
    print("✓ All data preserved correctly")
    
    # Verify weights
    assert profile_restored.preferences["colors"].items["black"].weight == profile.preferences["colors"].items["black"].weight
    print(f"✓ Weights preserved: black={profile_restored.preferences['colors'].items['black'].weight:.2f}")
    
    print("✅ TEST 9 PASSED\n")


def run_all_tests():
    """Run all validation tests."""
    print("\n" + "="*80)
    print(" PERSONALIZATION ENGINE - LOGIC VALIDATION TESTS")
    print(" (No Databricks/LLM Required)")
    print("="*80)
    
    tests = [
        ("PreferenceCategory Logic", test_preference_category),
        ("UserProfile Creation", test_user_profile),
        ("SessionState Management", test_session_state),
        ("PersonalizationRules Classification", test_personalization_rules),
        ("PreferenceCounter Logic", test_preference_counter),
        ("Post-Processing Functions", test_post_processing),
        ("Weight Boundary Enforcement", test_weight_boundaries),
        ("Disliked Items Management", test_disliked_items),
        ("Profile Serialization", test_profile_serialization),
    ]
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"❌ {test_name} FAILED: {e}\n")
            failed += 1
        except Exception as e:
            print(f"❌ {test_name} ERROR: {e}\n")
            failed += 1
    
    # Summary
    print("\n" + "="*80)
    print(" TEST SUMMARY")
    print("="*80)
    print(f"Total Tests: {passed + failed}")
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failed}")
    
    if failed == 0:
        print("\n🎉 ALL TESTS PASSED! The core logic is working correctly.")
    else:
        print(f"\n⚠️  {failed} test(s) failed. Please review the errors above.")
    
    print("="*80)
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
