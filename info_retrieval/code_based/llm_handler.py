
import os
import json
import logging
from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate
from models import (
    relevantFiles,
)
from typing import List, Dict
from pathlib import Path
import google.generativeai as genai

logger = logging.getLogger(__name__)

class LLMHandler:
    def __init__(self):
        self.llm = ChatOpenAI(
            model="gpt-4o",
            temperature=0.6,
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )
        self.structured_llm = self.llm.with_structured_output(relevantFiles, method="json_mode")
        genai.configure(api_key=os.getenv("GENAI_API_KEY"))
        self.model = genai.GenerativeModel(model_name="gemini-1.5-pro")

    def select_relevant_files(self, query: str, data_files: List[str]) -> List[str]:
        prompt_template = PromptTemplate(
            template="""
                You are an AI assistant. A user has asked the following query:

                "{query}"

                Given the list of data files below, select the files that are relevant to answering the query. Provide the file paths in a JSON array under the key "relevant_files".

                Data files:
                {data_files}

                ```json
                {{
                    "relevant_files": ["data/S3/s3_buckets.json", "data/IAM/iam_data.json"]
                }}
                ```
                """,
            input_variables=["query", "data_files"],
        )

        response = self.structured_llm.invoke(
            prompt_template.format(
                    query=query,
                    data_files=data_files,
                )
        )

        return response.relevant_files


    def answer_query_with_data(self, data: str, query: str) -> str:
        template=f"""
                You are an expert cloud architect. You are given a set of data and a user query. Your task is to answer the query using the provided data. Be precise and concise. 

                Query:
                {query}

                Data:
                {data}

            """
        
        response = self.model.generate_content(template)
        return response.text