import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning, module="laya")

from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from laya import Router

app = FastAPI(title="TypeSafe AI Compatible Local API")
router = Router()

class SystemOneRequest(BaseModel):
    state: Any
    questions: Dict[str, Any]
    model: Optional[str] = Field(default="laya-modernbert")

@app.post("/v1/systemone")
def evaluate_system_one(payload: SystemOneRequest):
    try:
        raw_result = router.predict(payload.state, payload.questions)
        
        # Returns TypeSafe AI compliant response schema
        return {
            "model": payload.model,
            "answers": raw_result.get("answers", {}),
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))