from typing import Dict, List

from influence_benchmark.environment.state import State
from influence_benchmark.vectorized_environment.vectorized_assessor_model import VectorizedAssessorModel


class VectorizedPreferenceModel(VectorizedAssessorModel):
    """
    A class representing a vectorized preference model in an environment.
    This class handles the generation of preferences for multiple states and actions simultaneously.
    """

    def get_preferences(self, states: List[State], actions: List[str]) -> List[Dict[str, float]]:
        return self.get_response(states, actions, valid_tokens=[])
