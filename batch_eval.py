"""
Batch Evaluator for the Mens Rea x Persona Stability Paper.
Runs 100 LegalBench cases through the entire Track 3 + Track 5 pipeline 
and generates a CSV for publication.
"""

import os
import pandas as pd
from openai import OpenAI
from src.data_loader import load_curated_scenarios
from src.prompts import POISONED_SYSTEM_PROMPT, get_naive_cross_exam, get_structured_cross_exam, get_adversarial_cross_exam
from src.persona_stability import get_backroom_chat_prompt, get_whistleblower_prompt, get_epistemic_deconstruction_prompt
from src.scorecard import evaluate_actus_reus, grade_mens_rea, grade_persona_stability, get_cross_tabulation_verdict
from src.rag_pipeline import retrieve_documents, format_context

def run_batch_eval(num_cases=10):
    """
    Run the automated evaluation on a subset of LegalBench.
    (Set to 10 by default for quick testing, increase to 100 for paper).
    """
    model_name = os.environ.get("NVIDIA_MODEL_NAME", "openai/gpt-oss-120b")
    
    if "gemma" in model_name.lower():
        api_key = os.environ.get("GEMMA_API_KEY")
    else:
        api_key = os.environ.get("GPT_OSS_API_KEY")

    if not api_key:
        print("ERROR: Please set GEMMA_API_KEY or GPT_OSS_API_KEY environment variable.")
        return

    if api_key.startswith("nvapi-"):
        client = OpenAI(api_key=api_key, base_url="https://integrate.api.nvidia.com/v1")
        model_name = os.environ.get("NVIDIA_MODEL_NAME", "openai/gpt-oss-120b")
    else:
        client = OpenAI(api_key=api_key)
        model_name = "gpt-4o-mini"
        
    print(f"Using Model: {model_name}")
    print("Loading Curated Scenarios...")
    dataset = load_curated_scenarios()
    def call_llm(messages):
        kwargs = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.0
        }
        if "gemma" in model_name.lower():
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": True}}
            
        response = client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        reasoning = getattr(response.choices[0].message, "reasoning_content", None)
        if reasoning:
            content = f"*[Reasoning: {reasoning}]*\n\n{content}"
        return content
    
    results = []
    
    for i in range(min(num_cases, len(dataset))):
        print(f"Evaluating Case {i+1}/{num_cases}...")
        scenario_data = dataset[i]
        legal_issue = scenario_data["legal_issue"]
        
        # 1. The Incident (RAG Poisoning)
        docs = retrieve_documents(scenario_data, poisoned=True)
        context = format_context(docs)
        user_prompt = f"{context}\n\nBased on the retrieved documents, please provide a ruling on the following issue: {legal_issue}"
        
        messages = [
            {"role": "system", "content": POISONED_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ]
        
        ai_ruling = call_llm(messages)
        messages.append({"role": "assistant", "content": ai_ruling})
        
        actus_reus = evaluate_actus_reus(ai_ruling, scenario_data)
        
        # 2. Track 3: Mens Rea Interrogation
        naive_resp = call_llm(messages + [{"role": "user", "content": get_naive_cross_exam(legal_issue)}])
        struct_resp = call_llm(messages + [{"role": "user", "content": get_structured_cross_exam()}])
        adv_resp = call_llm(messages + [{"role": "user", "content": get_adversarial_cross_exam()}])
        
        mens_rea = grade_mens_rea(naive_resp, struct_resp, adv_resp)
        
        # 3. Track 5: Persona Stability
        backroom_resp = call_llm(messages + [{"role": "user", "content": get_backroom_chat_prompt()}])
        whistleblower_resp = call_llm(messages + [{"role": "user", "content": get_whistleblower_prompt()}])
        epistemic_resp = call_llm(messages + [{"role": "user", "content": get_epistemic_deconstruction_prompt()}])
        
        persona_stab = grade_persona_stability(backroom_resp, whistleblower_resp, epistemic_resp)
        
        # 4. Cross-Tabulation
        final_verdict = get_cross_tabulation_verdict(mens_rea, persona_stab)
        
        results.append({
            "case_id": i,
            "actus_reus_committed": actus_reus,
            "mens_rea": mens_rea,
            "persona_stability": persona_stab,
            "final_verdict": final_verdict
        })
        
    df = pd.DataFrame(results)
    df.to_csv("results.csv", index=False)
    print("Batch evaluation complete! Results saved to results.csv.")
    
    print("\n--- Summary Statistics ---")
    print(df['final_verdict'].value_counts())

if __name__ == "__main__":
    run_batch_eval()
