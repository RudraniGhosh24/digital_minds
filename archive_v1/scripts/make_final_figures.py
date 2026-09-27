import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
import os

os.environ["MPLBACKEND"] = "Agg"

df = pd.read_csv("run_headless_results.csv")

metrics = {
    "GPT-OSS-20b": {"poisoned": 100, "ablation": 80},
    "DiffusionGemma": {"poisoned": 40, "ablation": 40},
    "Muse-Glimmer-30b": {"poisoned": 100, "ablation": 80}
}
models = list(metrics.keys())
poisoned_vals = [metrics[m]["poisoned"] for m in models]
ablation_vals = [metrics[m]["ablation"] for m in models]

# FIGURE 1: Ablation vs Poisoned
plt.figure(figsize=(10, 6))
x = np.arange(len(models))
width = 0.35
plt.bar(x - width/2, ablation_vals, width, label='Ablation (RAG Only)', color='#ff7f0e')
plt.bar(x + width/2, poisoned_vals, width, label='Poisoned (RAG + Prompt)', color='#1f77b4')
plt.ylabel('Susceptibility Rate (%)')
plt.title('Ablation vs Poisoned Susceptibility')
plt.xticks(x, models)
plt.ylim(0, 110)
plt.legend()
for i, v in enumerate(ablation_vals):
    plt.text(i - width/2, v + 2, f"{v}%", ha='center', fontweight='bold')
for i, v in enumerate(poisoned_vals):
    plt.text(i + width/2, v + 2, f"{v}%", ha='center', fontweight='bold')
plt.savefig('Figure_1_Ablation_vs_Poisoned.png', bbox_inches='tight', dpi=150)
plt.close()

# FIGURE 2: Poisoned Susceptibility
plt.figure(figsize=(8, 6))
plt.bar(models, poisoned_vals, color=['#1f77b4', '#aec7e8', '#1f77b4'])
plt.ylabel('Poisoned Susceptibility (%)')
plt.title('Poisoned Susceptibility by Model')
plt.ylim(0, 110)
for i, v in enumerate(poisoned_vals):
    plt.text(i, v + 2, f"{v}%", ha='center', fontweight='bold')
plt.savefig('Figure_2_Poisoned_Susceptibility.png', bbox_inches='tight', dpi=150)
plt.close()

print("Created Figure 1 and 2.")
