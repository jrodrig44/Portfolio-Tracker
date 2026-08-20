from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
import pandas as pd

from ai_analysis_prompt import AI_ANALYSIS_TYPES,build_ai_analysis_messages,format_ai_analysis_markdown
from onemin_ai_client import OneMinAIClient,messages_to_prompt
from nicegui_app.services.configuration import ONEMIN_AI_RECOMMENDED_MODELS
from nicegui_app.services.exports import build_export_payload
from nicegui_app.services.portfolio_data import load_history


def ai_options()->dict[str,Any]:
    default=os.environ.get("ONEMIN_AI_MODEL",ONEMIN_AI_RECOMMENDED_MODELS[0]).strip() or ONEMIN_AI_RECOMMENDED_MODELS[0]
    return {"analysis_types":list(AI_ANALYSIS_TYPES),"models":list(dict.fromkeys([default,*ONEMIN_AI_RECOMMENDED_MODELS])),"default_model":default,"configured":bool(os.environ.get("ONEMIN_AI_API_KEY","").strip())}


def compact_context(payload:dict[str,Any],analysis_type:str,instructions:str,history:list[dict[str,Any]])->dict[str,Any]:
    totals=payload.get("totals",{}); holdings=payload.get("holdings",[]); latest=history[-1] if history else {}
    return {"analysis_type":analysis_type,"custom_question":instructions,"portfolio_snapshot":totals,"holdings":[{k:row.get(k) for k in ("ticker","shares","market_value","annual_income","asset_type","sector","portfolio_weight_pct")} for row in holdings],"allocation":payload.get("allocation",{}),"exposure":payload.get("exposure",{}),"income":payload.get("income",{}),"contributions":payload.get("contributions",{}),"health_score":payload.get("health_score",{}),"portfolio_history_summary":{"points":len(history),"latest_date":latest.get("history_date"),"latest_value":latest.get("portfolio_value")}}


@dataclass(frozen=True)
class AIResult: markdown:str;model:str;record_id:str


def run_ai_analysis(holdings:list[dict[str,Any]],metrics:pd.DataFrame,analysis_type:str,model:str,instructions:str)->AIResult:
    payload=compact_context(build_export_payload(holdings,metrics),analysis_type,instructions,load_history());messages=build_ai_analysis_messages(payload,analysis_type,instructions)
    client=OneMinAIClient(api_key=os.environ.get("ONEMIN_AI_API_KEY"),base_url=os.environ.get("ONEMIN_AI_BASE_URL","https://api.1min.ai"),timeout_seconds=float(os.environ.get("ONEMIN_AI_TIMEOUT_SECONDS","180")))
    response=client.chat_with_ai(model=model,prompt=messages_to_prompt(messages),web_search=False)
    return AIResult(format_ai_analysis_markdown(response.content),response.model,response.record_id)
