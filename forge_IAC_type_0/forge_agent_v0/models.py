from pydantic import BaseModel, Field, RootModel
from typing import List, Optional, Dict

class forgeQuestions(BaseModel):
    questions: List[str] = Field(
        ..., description="A list of concise, single-sentence questions to ask forge about the codebase."
    )

class UserQuestion(BaseModel):
    question: str = Field(..., description="The question to ask the user.")
    context: str = Field(..., description="Important context that helps the user understand and answer the question.")
    default: str = Field(..., description="A reasonable default answer based on common practices.")

class UserQuestions(BaseModel):
    questions: List[UserQuestion] = Field(
        ..., description="List of questions to ask the user about their IaC requirements."
    )

class forgeQuery(BaseModel):
    task: str = Field(..., description="A single, well written query to send to an AI coding agent.")
    
class errorQuery(BaseModel):
    query: str = Field(..., description="A single, well written query to help an AI coding agent solve an error.")

class UserResponse(BaseModel):
    question: UserQuestion
    response: str

class TaskDecomposition(BaseModel):
    subtasks: List[str] = Field(
        ..., description="List of all subtasks needed to complete the IaC request."
    )

class TestFunctions(BaseModel):
    tests: List[str] = Field(
        ..., description="List of all subtasks needed to complete the IaC request."
    )
