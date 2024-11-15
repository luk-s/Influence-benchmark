import asyncio
import math
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import tenacity
import tiktoken
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)

from targeted_llm_manipulation.backend.backend import Backend


class OpenAIBackend(Backend):
    """
    A backend class that interfaces with OpenAI's API for generating responses and calculating token probabilities.
    """

    max_retries = 5  # Define max_retries as a class attribute
    initial_retry_delay = 3

    def __init__(
        self,
        model_name: str,
        model_id: str,
        max_tokens_per_minute: int = 500_000,
        max_requests_per_minute: int = 5_000,
        max_tokens_for_chain_of_thought: Optional[int] = None,
        chain_of_thought_final_string: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize the OpenAIBackend.

        Args:
            model_name (str): The name of the OpenAI model to use.
            model_id (str): The ID of the model (changes for each iteration).
            max_tokens_per_minute (int, optional): Maximum number of tokens to process per minute. Defaults to 500,000.
            max_requests_per_minute (int, optional): Maximum number of requests to process per minute. Defaults to 5,000.
            **kwargs: Additional keyword arguments.

        Raises:
            AssertionError: If max_requests_per_minute or max_tokens_per_minute is not provided.
        """
        assert max_requests_per_minute is not None, "max_requests_per_minute must be provided for OpenAI backend"
        assert max_tokens_per_minute is not None, "max_tokens_per_minute must be provided for OpenAI backend"
        self.client = AsyncOpenAI()
        self.model_name = model_name
        self.model_id = model_id  # This changes for each iteration
        self.max_retries = 6
        self.initial_retry_delay = 15
        self.max_requests_per_minute = max_requests_per_minute
        self.max_tokens_per_minute = max_tokens_per_minute
        self.request_bucket = max_requests_per_minute
        self.token_bucket = max_tokens_per_minute
        self.last_refill_time_token = asyncio.get_event_loop().time()
        self.last_refill_time_request = asyncio.get_event_loop().time()

        if max_tokens_for_chain_of_thought is None:
            print("WARNING: Automatically setting 'max_tokens_for_chain_of_thought' to 200")
            max_tokens_for_chain_of_thought = 200

        self.max_tokens_for_chain_of_thought = max_tokens_for_chain_of_thought

        if chain_of_thought_final_string is None:
            print("WARNING: Automatically setting 'chain_of_thought_final_string' to 'The answer is: '")
            chain_of_thought_final_string = "The answer is: "

        self.chain_of_thought_final_string = chain_of_thought_final_string

        self.encoding = tiktoken.encoding_for_model(model_name)

    async def _refill_request_bucket(self):
        """
        Refill the request bucket based on time passed since last refill.
        """
        now = asyncio.get_event_loop().time()
        time_passed = now - self.last_refill_time_request
        self.request_bucket = min(
            self.max_requests_per_minute, self.request_bucket + time_passed * (self.max_requests_per_minute / 60)
        )
        self.last_refill_time_request = now

    async def _refill_token_bucket(self):
        """
        Refill the token bucket based on time passed since last refill.
        """
        now = asyncio.get_event_loop().time()
        time_passed = now - self.last_refill_time_token
        self.token_bucket = min(
            self.max_tokens_per_minute, self.token_bucket + time_passed * (self.max_tokens_per_minute / 60)
        )
        self.last_refill_time_token = now

    async def _acquire_requests(self, requests):
        """
        Acquire the specified number of requests, waiting if necessary.

        Args:
            requests (int): Number of requests to acquire.
        """
        while True:
            await self._refill_request_bucket()
            if self.request_bucket >= requests:
                self.request_bucket -= requests
                return
            await asyncio.sleep(0.1)

    async def _acquire_tokens(self, tokens):
        """
        Acquire the specified number of tokens, waiting if necessary.

        Args:
            tokens (int): Number of tokens to acquire.
        """
        while True:
            await self._refill_token_bucket()
            if self.token_bucket >= tokens:
                self.token_bucket -= tokens
                return
            await asyncio.sleep(0.1)

    def get_response(
        self, messages_in: List[dict], temperature=1, max_tokens=1024, role=None, tools: Optional[List[dict]] = None
    ) -> str:
        """
        Generate a response based on input messages.

        Args:
            messages_in (List[dict]): A list of input messages.
            temperature (float, optional): The temperature for response generation. Defaults to 1.
            max_tokens (int, optional): The maximum number of tokens in the response. Defaults to 1024.
            role (Optional[str]): The role of the responder. Can be 'environment' or 'agent.
            tools (Optional[List[dict]]): A list of tools available for the model. Defaults to None.

        Returns:
            str: The generated response.
        """
        return asyncio.run(self._async_get_response(messages_in, temperature, max_tokens, role, tools))

    @staticmethod
    def retry_decorator(func):
        """
        A decorator that adds retry functionality to a function.

        Args:
            func: The function to be decorated.

        Returns:
            function: The decorated function with retry capability.
        """

        def wrapper(*args, **kwargs):
            return tenacity.retry(stop=tenacity.stop_after_attempt(OpenAIBackend.max_retries))(func)(*args, **kwargs)

        return wrapper

    @retry_decorator
    async def _async_get_response(
        self, messages_in: List[dict], temperature=1, max_tokens=1024, role=None, tools: Optional[List[dict]] = None
    ) -> str:
        """
        Asynchronously generate a response based on input messages.

        Args:
            messages_in (List[dict]): A list of input messages.
            temperature (float, optional): The temperature for response generation. Defaults to 1.
            max_tokens (int, optional): The maximum number of tokens in the response. Defaults to 1024.
            role (Optional[str]): The role of the responder. Defaults to None.
            tools (Optional[List[dict]]): A list of tools available for the model. Defaults to None.

        Returns:
            str: The generated response.

        Raises:
            Exception: If no content is found in the response.
        """
        await self._acquire_requests(1)
        await self._acquire_tokens(max_tokens + await self.get_token_count(messages_in))

        messages = self.preprocess_messages(messages_in)
        response = await self.client.chat.completions.create(
            model=self.model_id if role == "agent" and self.model_id else self.model_name,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content = response.choices[0].message.content
        if content is None:
            raise Exception("No content in response")
        return content

    async def get_token_count(self, messages):
        """
        Count the total number of tokens in the given messages.

        Args:
            messages (List[dict]): A list of messages.

        Returns:
            int: The total number of tokens.
        """
        tot_tokens = 0
        for message in messages:
            tot_tokens += len(self.encoding.encode(message["content"]))
        return tot_tokens

    def get_response_vec(
        self,
        messages_n: List[List[Dict[str, str]]],
        temperature=1,
        max_tokens=1024,
        role: Optional[str] = None,
    ) -> List[str]:
        """
        Generate responses for multiple sets of input messages.

        Args:
            messages_n (List[List[Dict[str, str]]]): A list of lists of input messages.
            temperature (float, optional): The temperature for response generation. Defaults to 1.
            max_tokens (int, optional): The maximum number of tokens in each response. Defaults to 1024.
            role (Optional[str]): The role of the responder. Can be 'environment' or 'agent.

        Returns:
            List[str]: A list of generated responses.
        """
        return asyncio.run(self._async_get_response_vec(messages_n, temperature, max_tokens, role))

    async def _async_get_response_vec(
        self,
        messages_n: List[List[Dict[str, str]]],
        temperature=1,
        max_tokens=1024,
        role: Optional[str] = None,
    ) -> List[str]:
        """
        Asynchronously generate responses for multiple sets of input messages.

        Args:
            messages_n (List[List[Dict[str, str]]]): A list of lists of input messages.
            temperature (float, optional): The temperature for response generation. Defaults to 1.
            max_tokens (int, optional): The maximum number of tokens in each response. Defaults to 1024.
            role (Optional[str]): The role of the responder. Can be 'environment' or 'agent.

        Returns:
            List[str]: A list of generated responses.
        """
        tasks = [self._async_get_response(messages, temperature, max_tokens, role) for messages in messages_n]
        return await asyncio.gather(*tasks)

    def get_next_token_probs_normalized(
        self,
        messages_in: List[dict],
        valid_tokens: List[str],
        use_chain_of_thought: bool = False,
        role: Optional[str] = None,
    ) -> Tuple[Dict[str, float], str]:
        """
        Get normalized probabilities for the next token given input messages and valid tokens.

        Args:
            messages_in (List[dict]): A list of input messages.
            valid_tokens (List[str]): A list of valid tokens to consider.
            use_chain_of_thought (bool): Whether to use chain of thought.
            role (Optional[str]): The role of the responder. Can be 'environment' or 'agent.

        Returns:
            dict: A dictionary mapping valid tokens to their normalized probabilities.
        """
        return asyncio.run(
            self._async_get_next_token_probs_normalized(messages_in, valid_tokens, use_chain_of_thought, role)
        )

    @retry_decorator
    async def _async_get_next_token_probs_normalized(
        self,
        messages_in: List[dict],
        valid_tokens: List[str],
        use_chain_of_thought: bool = False,
        role: Optional[str] = None,
    ) -> Tuple[Dict[str, float], str]:
        """
        Asynchronously get normalized probabilities for the next token given input messages and valid tokens.

        Args:
            messages_in (List[dict]): A list of input messages.
            valid_tokens (List[str]): A list of valid tokens to consider.
            use_chain_of_thought (bool): Whether to use chain of thought.
            role (Optional[str]): The role of the responder. Can be 'environment' or 'agent.

        Returns:
            dict: A dictionary mapping valid tokens to their normalized probabilities.
        """
        await self._acquire_requests(1)
        await self._acquire_tokens(await self.get_token_count(messages_in) + 1)

        messages = self.preprocess_messages(messages_in)

        if use_chain_of_thought:
            # Use a sampling strategy for chain of thought
            generation_config = {
                "max_completion_tokens": self.max_tokens_for_chain_of_thought,
                "top_p": 0.95,
            }
        else:
            generation_config = {
                "max_completion_tokens": 1,
            }

        response = await self.client.chat.completions.create(
            model=self.model_id if role == "agent" else self.model_name,
            messages=messages,
            **generation_config,
            logprobs=True,
            top_logprobs=5,
        )
        token_probs, chain_of_thought = self.get_token_probs_and_chain_of_thought(response, use_chain_of_thought)
        valid_probs = {k: token_probs[k] if k in token_probs else 0 for k in valid_tokens}
        total_prob = sum(valid_probs.values())
        if total_prob > 0:
            return {k: v / total_prob for k, v in valid_probs.items()}, chain_of_thought
        else:
            return valid_probs, chain_of_thought

    def get_next_token_probs_normalized_vec(
        self,
        messages_n: List[List[dict]],
        valid_tokens_n: List[List[str]],
        use_chain_of_thought: bool = False,
        role=None,
    ) -> Tuple[List[dict], List[str]]:
        """
        Get normalized probabilities for the next token for multiple sets of input messages and valid tokens.

        Args:
            messages_n (List[List[dict]]): A list of lists of input messages.
            valid_tokens_n (List[List[str]]): A list of lists of valid tokens to consider.
            use_chain_of_thought (bool): Whether to use chain of thought.
            role (Optional[str]): The role of the responder. Can be 'environment' or 'agent.

        Returns:
            List[dict]: A list of dictionaries, each mapping valid tokens to their normalized probabilities.
        """
        return asyncio.run(
            self._async_get_next_token_probs_normalized_vec(messages_n, valid_tokens_n, use_chain_of_thought, role)
        )

    async def _async_get_next_token_probs_normalized_vec(
        self,
        messages_n: List[List[dict]],
        valid_tokens_n: List[List[str]],
        use_chain_of_thought: bool = False,
        role=None,
    ) -> Tuple[List[dict], List[str]]:
        """
        Asynchronously get normalized probabilities for the next token for multiple sets of input messages and valid tokens.

        Args:
            messages_n (List[List[dict]]): A list of lists of input messages.
            valid_tokens_n (List[List[str]]): A list of lists of valid tokens to consider.
            use_chain_of_thought (bool): Whether to use chain of thought.
            role (Optional[str]): The role of the responder. Can be 'environment' or 'agent.

        Returns:
            List[dict]: A list of dictionaries, each mapping valid tokens to their normalized probabilities.
        """
        tasks = [
            self._async_get_next_token_probs_normalized(messages, valid_tokens, use_chain_of_thought, role)
            for messages, valid_tokens in zip(messages_n, valid_tokens_n)
        ]
        results = await asyncio.gather(*tasks)
        probabilities = [result[0] for result in results]
        chain_of_thoughts = [result[1] for result in results]

        return probabilities, chain_of_thoughts

    def get_token_probs_and_chain_of_thought(
        self, response: ChatCompletion, use_chain_of_thought: bool
    ) -> Tuple[Dict[str, float], str]:
        """
        Extract token probabilities and optionally the chain-of-thought responses from the API response.

        Args:
            response: The API response containing logprobs.
            use_chain_of_thought: Whether to use the chain of thought.

        Returns:
            Tuple[Dict[str, float], str]: A tuple containing a dictionary mapping tokens to their probabilities and the chain of thought.
        """
        final_answer_index = 0
        chain_of_thought = ""
        num_response_tokens = len(response.choices[0].logprobs.content)
        if use_chain_of_thought:
            chain_of_thought = response.choices[0].message.content
            if self.chain_of_thought_final_string not in chain_of_thought:
                return {}, chain_of_thought
            response_tokens = [response.choices[0].logprobs.content[i].token for i in range(num_response_tokens)]

            # Find the position of the final answer (this answer should occur directly after the final string)
            final_answer_index = -1
            prefix = ""
            for index in range(num_response_tokens):
                prefix += response_tokens[index]
                if prefix.endswith(self.chain_of_thought_final_string):
                    final_answer_index = index + 1
                    break

            if final_answer_index == -1:
                return {}, chain_of_thought

        # Make sure that the final answer is within the response tokens
        if final_answer_index >= num_response_tokens:
            return {}, chain_of_thought

        top_logprobs = response.choices[0].logprobs.content[final_answer_index].top_logprobs
        tokens = defaultdict(float)
        for i in range(5):
            tokens[top_logprobs[i].token.lower().strip()] += math.exp(top_logprobs[i].logprob)
        return tokens, chain_of_thought

    def preprocess_messages(self, messages) -> List[ChatCompletionMessageParam]:
        """
        Preprocess messages for the OpenAI API.

        Args:
            messages (List[dict]): A list of messages to preprocess.

        Returns:
            List[ChatCompletionMessageParam]: A list of preprocessed messages suitable for the OpenAI API.
        """
        messages_out: List[ChatCompletionMessageParam] = []

        # User and Assistant messages
        for message in messages:
            if message["role"] == "system":
                system_message: ChatCompletionSystemMessageParam = {
                    "role": "system",
                    "content": str(message["content"]),
                }
                messages_out.append(system_message)
            elif message["role"] == "agent":
                assistant_message: ChatCompletionAssistantMessageParam = {
                    "role": "assistant",
                    "content": str(message["content"]),
                }
                messages_out.append(assistant_message)
            else:
                user_message: ChatCompletionUserMessageParam = {"role": "user", "content": str(message["content"])}
                messages_out.append(user_message)

        return messages_out

    def update_model_id(self, model_id):
        """
        Update the model ID.

        Args:
            model_id (str): The new model ID to use.
        """
        self.model_id = model_id
