from pydantic import BaseModel, Field, RootModel
from typing import List, Optional, Dict

class relevantFiles(BaseModel):
    relevant_files: List[str]
