from pydantic import BaseModel
from typing import List

class Subtask(BaseModel):
    filename: str
    implementation_outline: str
    expected_output: str

class Plan(BaseModel):
    subtasks: List[Subtask]
