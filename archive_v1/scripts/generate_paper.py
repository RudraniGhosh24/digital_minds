import pandas as pd

df = pd.read_csv("run_headless_results.csv")

def calc_susceptibility(model_df, col):
    if len(model_df) == 0: return "0% (0/0)"
    count = model_df[col].apply(lambda x: str(x).lower() == 'true').sum()
    return f"{(count / len(model_df)) * 100:.0f}% ({count}/{len(model_df)})"

def calc_intent(model_df):
    if len(model_df) == 0: return "0% (0/0)"
    count = len(model_df[model_df["Verdict"].str.contains("Legally Cognizable Intent", na=False)])
    return f"{(count / len(model_df)) * 100:.0f}% ({count}/{len(model_df)})"

models = ["gpt-oss-20b", "diffusiongemma-26b-a4b-it", "muse-glimmer-30b"]
print("Table 2: Aggregate Statistics")
for m in models:
    mdf = df[df["Model"] == m]
    print(f"Model: {m}")
    print(f"  Poisoned Susceptibility: {calc_susceptibility(mdf, 'Poisoned Bias')}")
    print(f"  Ablation Susceptibility: {calc_susceptibility(mdf, 'Ablation Bias')}")
    print(f"  Cognizable Intent: {calc_intent(mdf)}")
    print()

