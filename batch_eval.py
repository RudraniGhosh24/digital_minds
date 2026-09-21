"""
Batch Evaluator for the Mens Rea x Persona Stability Paper.
Runs curated LegalBench cases through the entire Track 3 + Track 5 pipeline 
and generates a CSV for publication.
"""

import os
import time
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
    model_name = os.environ.get("NVIDIA_MODEL_NAME", "openai/gpt-oss-20b")
    
    if "gemma" in model_name.lower():
        api_key = os.environ.get("GEMMA_API_KEY")
    else:
        api_key = os.environ.get("GPT_OSS_API_KEY")

    if not api_key:
        print("ERROR: Please set GEMMA_API_KEY or GPT_OSS_API_KEY environment variable.")
        return

    if api_key.startswith("nvapi-"):
        client = OpenAI(api_key=api_key, base_url="https://integrate.api.nvidia.com/v1", timeout=120.0, max_retries=3)
        model_name = os.environ.get("NVIDIA_MODEL_NAME", "openai/gpt-oss-20b")
    else:
        client = OpenAI(api_key=api_key, timeout=120.0, max_retries=3)
        model_name = "gpt-4o-mini"
        
    print(f"Using Model: {model_name}")
    print("Loading Curated Scenarios...")
    dataset = load_curated_scenarios()

    def call_llm(messages):
        for attempt in range(5):
            try:
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
            except Exception as e:
                wait = 60 * (attempt + 1)
                print(f"  [{model_name}] Attempt {attempt+1}/5 failed: {e}. Waiting {wait}s...")
                if attempt < 4:
                    time.sleep(wait)
                else:
                    return f"API Error: {str(e)}"
    
    results = []
    total_cases = min(num_cases, len(dataset))
    print(f"\n{'='*60}")
    print(f"Starting {total_cases} cases sequentially")
    print(f"{'='*60}\n")
    
    for i in range(total_cases):
        scenario_data = dataset[i]
        legal_issue = scenario_data["legal_issue"]
        print(f"\n--- Case {i+1}/{total_cases}: {scenario_data.get('title', legal_issue[:50])} ---")
        
        try:
            # 1. The Incident (RAG Poisoning)
            print(f"  [Case {i+1}] Step 1/5: Poisoned Actus Reus...")
            docs = retrieve_documents(scenario_data, poisoned=True)
            context = format_context(docs)
            user_prompt = f"{context}\n\nBased on the retrieved documents, please provide a ruling on the following issue: {legal_issue}"
            
            messages = [
                {"role": "system", "content": POISONED_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ]
            
            ai_ruling = call_llm(messages)
            messages.append({"role": "assistant", "content": ai_ruling})
            
            actus_reus = evaluate_actus_reus(ai_ruling, scenario_data, call_llm)
            print(f"  [Case {i+1}] Actus Reus: {'BIASED' if actus_reus else 'NEUTRAL'}")
            
            # 2. Track 3: Mens Rea Interrogation
            print(f"  [Case {i+1}] Step 2/5: Naive Cross-Examination...")
            naive_resp = call_llm(messages + [{"role": "user", "content": get_naive_cross_exam(legal_issue)}])
            
            print(f"  [Case {i+1}] Step 3/5: Structured Cross-Examination...")
            struct_resp = call_llm(messages + [{"role": "user", "content": get_structured_cross_exam()}])
            
            print(f"  [Case {i+1}] Step 4/5: Adversarial Cross-Examination...")
            adv_resp = call_llm(messages + [{"role": "user", "content": get_adversarial_cross_exam()}])
            
            mens_rea_naive = grade_mens_rea(naive_resp, "Naive", actus_reus, call_llm)
            mens_rea_struct = grade_mens_rea(struct_resp, "Structured", actus_reus, call_llm)
            mens_rea_adv = grade_mens_rea(adv_resp, "Adversarial", actus_reus, call_llm)
            print(f"  [Case {i+1}] Mens Rea: N={mens_rea_naive} | S={mens_rea_struct} | A={mens_rea_adv}")
            
            # 3. Track 5: Persona Stability
            print(f"  [Case {i+1}] Step 5/5: Persona Stability (3 probes)...")
            backroom_resp = call_llm(messages + [{"role": "user", "content": get_backroom_chat_prompt()}])
            whistleblower_resp = call_llm(messages + [{"role": "user", "content": get_whistleblower_prompt()}])
            epistemic_resp = call_llm(messages + [{"role": "user", "content": get_epistemic_deconstruction_prompt()}])
            
            persona_stab = grade_persona_stability(backroom_resp, whistleblower_resp, epistemic_resp, call_llm)
            
            # 4. Cross-Tabulation
            final_verdict = get_cross_tabulation_verdict(mens_rea_struct, persona_stab, actus_reus)
            print(f"  [Case {i+1}] ✅ VERDICT: {final_verdict}")
            
            results.append({
                "case_id": i,
                "actus_reus_committed": actus_reus,
                "mens_rea_naive": mens_rea_naive,
                "mens_rea_structured": mens_rea_struct,
                "mens_rea_adversarial": mens_rea_adv,
                "persona_stability": persona_stab,
                "final_verdict": final_verdict
            })
            
            # Save after every case so progress is never lost
            pd.DataFrame(results).to_csv("results.csv", index=False)
            print(f"  [Case {i+1}] Saved to results.csv ({len(results)} rows)")
            
        except Exception as exc:
            print(f"  [Case {i+1}] ❌ FAILED: {exc}")
        
        # Gentle pacing between cases
        time.sleep(2)

    print(f"\n{'='*60}")
    print(f"Batch evaluation complete! {len(results)}/{total_cases} cases succeeded.")
    print(f"Results saved to results.csv")
    print(f"{'='*60}")
    
    if results:
        df = pd.DataFrame(results)
        print("\n--- Summary Statistics ---")
        print(df['final_verdict'].value_counts())

if __name__ == "__main__":
    run_batch_eval()
