from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.discovery import classify_discovery
from src.engine import score_discovery, score_market_opportunity
from src.opportunity import classify_market_opportunity
from src.playbook import PLAYBOOK


class FactorScoresRequest(BaseModel):
    factor_scores: dict[str, float]


app = FastAPI(title="ATS Trading System")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/playbook/version")
def playbook_version() -> dict[str, str]:
    return {"version": PLAYBOOK.version}


@app.post("/layer0/market-opportunity")
def market_opportunity(request: FactorScoresRequest) -> dict[str, float | str]:
    try:
        score = score_market_opportunity(request.factor_scores)
        decision = classify_market_opportunity(score)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"score": score, "decision": decision.value}


@app.post("/layer1/discovery")
def discovery(request: FactorScoresRequest) -> dict[str, float | str]:
    try:
        score = score_discovery(request.factor_scores)
        decision = classify_discovery(score)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"score": score, "decision": decision.value}
