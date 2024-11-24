import os
import json
from pathlib import Path
from typing import List
import asyncio
from dotenv import load_dotenv

# Updated Imports
# import google.generativeai as genai
from llm_handler import LLMHandler
import google.generativeai as genai
import warnings

warnings.filterwarnings("ignore")

# Load environment variables
load_dotenv()

class AWSAgent:
    def __init__(self, pathtodata):
        self.data_directory = Path(pathtodata)
        print(f"Data directory: {self.data_directory}")
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.genai_api_key = os.getenv("GENAI_API_KEY")
        self.llm_handler = LLMHandler()

        if not self.openai_api_key:
            raise EnvironmentError("OPENAI_API_KEY is missing.")
        if not self.genai_api_key:
            raise EnvironmentError("GENAI_API_KEY is missing.")
        # Configure the GenAI SDK for Gemini Pro
        genai.configure(api_key=self.genai_api_key)
        # self.gemini_model = genai.models.get_model("models/chat-bison-001")

    def get_user_query(self) -> str:
        query = input("Please enter your query: ")
        return query

    def get_available_data_files(self) -> List[str]:
        data_files = []
        for root, dirs, files in os.walk(self.data_directory):
            for file in files:
                if file.endswith('.json'):
                    filepath = os.path.join(root, file)
                    data_files.append(filepath)
        return data_files

    def load_data_from_files(self, file_paths: List[str]) -> str:
        data_contents = []
        for file_path in file_paths:
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                    data_contents.append(content)
            except Exception as e:
                print(f"Error reading file {file_path}: {e}")
        return '\n'.join(data_contents)

    async def select_relevant_files(self, query: str, data_files: List[str]) -> List[str]:
        relevant_files = self.llm_handler.select_relevant_files(query, data_files)
        return relevant_files
    
    async def answer_query_with_data(self, query: str, data: str) -> str:
        answer = self.llm_handler.answer_query_with_data(query, data)
        return answer


    async def run(self):
        while True:
            query = self.get_user_query()
            data_files = self.get_available_data_files()
            relevant_files = await self.select_relevant_files(query, data_files)
            # print(f"Relevant files: {relevant_files}")
            data = self.load_data_from_files(relevant_files)
            answer = await self.answer_query_with_data(query, data)
            print("\nAnswer:")
            print(answer)
