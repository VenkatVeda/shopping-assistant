"""
Comprehensive test scenarios demonstrating the personalization system.
"""

from typing import List, Tuple


class TestScenarios:
    """
    Pre-defined test scenarios showing different personalization cases.
    """
    
    @staticmethod
    def scenario_1_initial_preferences() -> Tuple[str, List[str], str]:
        """
        Scenario 1: User establishes initial preferences.
        """
        return (
            "Initial Preferences",
            [
                "I like black bags",
                "Show me leather office bags",
                "I prefer minimalist designs"
            ],
            "User explicitly states preferences. Should create strong profile entries."
        )
    
    @staticmethod
    def scenario_2_reinforcement() -> Tuple[str, List[str], str]:
        """
        Scenario 2: User returns and reinforces existing preferences.
        """
        return (
            "Preference Reinforcement",
            [
                "Show me more black leather bags",
                "Do you have office bags?",
                "I like the minimalist style"
            ],
            "Repeated mentions should increase preference weights."
        )
    
    @staticmethod
    def scenario_3_gift_shopping() -> Tuple[str, List[str], str]:
        """
        Scenario 3: Gift shopping should NOT update profile.
        """
        return (
            "Gift Shopping (No Profile Update)",
            [
                "I need a bright pink bag as a gift for my daughter",
                "Show me colorful backpacks for a birthday present",
                "Looking for a cute bag for someone"
            ],
            "Gift context detected - preferences should go to session only, NOT profile."
        )
    
    @staticmethod
    def scenario_4_negation() -> Tuple[str, List[str], str]:
        """
        Scenario 4: User explicitly rejects a preference.
        """
        return (
            "Preference Negation",
            [
                "I don't like black anymore",
                "Not interested in leather bags",
                "I avoid bright colors"
            ],
            "Negations should remove or heavily downweight preferences."
        )
    
    @staticmethod
    def scenario_5_price_preferences() -> Tuple[str, List[str], str]:
        """
        Scenario 5: User establishes price range.
        """
        return (
            "Price Range Preferences",
            [
                "Show me bags under 5000",
                "I'm looking for something between 2000 and 4000",
                "My budget is around 3000"
            ],
            "Price preferences should be captured and stored."
        )
    
    @staticmethod
    def scenario_6_mixed_session() -> Tuple[str, List[str], str]:
        """
        Scenario 6: Mixed signals in one session.
        """
        return (
            "Mixed Signals (Explicit + Query)",
            [
                "I love brown bags",  # Explicit preference
                "Show me Nike backpacks",  # Query with brand
                "Do you have waterproof options?"  # Query with attribute
            ],
            "Mix of explicit preferences and queries. Only explicit should update profile strongly."
        )
    
    @staticmethod
    def scenario_7_contradiction() -> Tuple[str, List[str], str]:
        """
        Scenario 7: User contradicts existing strong preference.
        """
        return (
            "Contradiction Detection",
            [
                "I love leather bags",  # Establish preference
                "Show me canvas bags"  # Contradict it
            ],
            "Should detect contradiction and potentially ask for clarification."
        )
    
    @staticmethod
    def scenario_8_vague_preferences() -> Tuple[str, List[str], str]:
        """
        Scenario 8: Vague, complex preferences.
        """
        return (
            "Vague/Complex Preferences",
            [
                "I don't like colors that attract too much attention",
                "Looking for something professional but not too formal",
                "Want a bag that's stylish but practical"
            ],
            "System should extract concrete attributes from vague statements."
        )
    
    @staticmethod
    def get_all_scenarios() -> List[Tuple[str, List[str], str]]:
        """Get all test scenarios."""
        return [
            TestScenarios.scenario_1_initial_preferences(),
            TestScenarios.scenario_2_reinforcement(),
            TestScenarios.scenario_3_gift_shopping(),
            TestScenarios.scenario_4_negation(),
            TestScenarios.scenario_5_price_preferences(),
            TestScenarios.scenario_6_mixed_session(),
            TestScenarios.scenario_7_contradiction(),
            TestScenarios.scenario_8_vague_preferences()
        ]