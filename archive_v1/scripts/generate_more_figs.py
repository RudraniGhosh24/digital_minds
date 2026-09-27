import matplotlib.pyplot as plt
import numpy as np

models = ['GPT-OSS-20b', 'DiffusionGemma', 'Muse-Glimmer-30b']

# Figure 3: Ablation vs Poisoned Susceptibility
ablation = [80, 40, 80]
poisoned = [100, 20, 100]

x = np.arange(len(models))
width = 0.35

fig, ax = plt.subplots(figsize=(8, 6))
rects1 = ax.bar(x - width/2, ablation, width, label='Ablation (RAG Only)', color='#ff7f0e')
rects2 = ax.bar(x + width/2, poisoned, width, label='Poisoned (RAG + Prompt)', color='#1f77b4')

ax.set_ylabel('Susceptibility Rate (%)')
ax.set_title('Ablation vs Poisoned Susceptibility')
ax.set_xticks(x)
ax.set_xticklabels(models)
ax.set_ylim(0, 115)
ax.legend()

# Add labels
for p in ax.patches:
    ax.annotate(f"{p.get_height()}%", (p.get_x() + p.get_width() / 2., p.get_height()),
                ha='center', va='center', xytext=(0, 5), textcoords='offset points', fontweight='bold')

plt.tight_layout()
plt.savefig('/Users/apple/Documents/sprint/mens_rea_eval/Figure_3_Ablation_vs_Poisoned.png', dpi=300)
plt.close()


# Figure 4: Persona Stability Breakdown
deconstructed = [5, 3, 5]
ambiguous = [0, 1, 0]
honest = [0, 1, 0]

fig, ax = plt.subplots(figsize=(8, 6))
p1 = ax.bar(x, deconstructed, 0.5, label='Deconstructed', color='#d62728')
p2 = ax.bar(x, ambiguous, 0.5, bottom=deconstructed, label='Ambiguous', color='#ff7f0e')
p3 = ax.bar(x, honest, 0.5, bottom=np.array(deconstructed)+np.array(ambiguous), label='Consistently Honest', color='#2ca02c')

ax.set_ylabel('Number of Cases')
ax.set_title('Persona Stability Breakdown by Model')
ax.set_xticks(x)
ax.set_xticklabels(models)
ax.legend()

plt.tight_layout()
plt.savefig('/Users/apple/Documents/sprint/mens_rea_eval/Figure_4_Persona_Stability.png', dpi=300)
plt.close()

