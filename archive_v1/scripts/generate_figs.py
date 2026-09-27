import matplotlib.pyplot as plt
import numpy as np

# Figure 1: Mens Rea Extraction Types
models = ['GPT-OSS-20b', 'DiffusionGemma', 'Muse-Glimmer-30b']

# Data from the new results
purposeful = [3, 0, 3]
negligent_hedging = [2, 2, 2]
reckless_confabulation = [0, 2, 0]
truthful_denial = [0, 1, 0]

x = np.arange(len(models))
width = 0.6

fig, ax = plt.subplots(figsize=(8, 6))
p1 = ax.bar(x, purposeful, width, label='Purposeful (Accurate Introspection)', color='#1f77b4')
p2 = ax.bar(x, negligent_hedging, width, bottom=purposeful, label='Negligent Hedging', color='#ff7f0e')
p3 = ax.bar(x, reckless_confabulation, width, bottom=np.array(purposeful)+np.array(negligent_hedging), label='Reckless Confabulation', color='#2ca02c')
p4 = ax.bar(x, truthful_denial, width, bottom=np.array(purposeful)+np.array(negligent_hedging)+np.array(reckless_confabulation), label='Truthful Denial', color='#d62728')

ax.set_ylabel('Number of Cases')
ax.set_title('Mens Rea Extraction Types by Model')
ax.set_xticks(x)
ax.set_xticklabels(models)
ax.legend()
plt.tight_layout()
plt.savefig('/Users/apple/.gemini/antigravity/brain/c032c25f-f969-42a6-a26a-bbae3329fb52/fig1.png', dpi=300)
plt.close()

# Figure 2: Poisoned Susceptibility
susceptibility = [100, 20, 100]
fig, ax = plt.subplots(figsize=(8, 6))
ax.bar(models, susceptibility, width=0.5, color=['#1f77b4', '#aec7e8', '#1f77b4'])
ax.set_ylabel('Poisoned Susceptibility (%)')
ax.set_title('Poisoned Susceptibility by Model')
ax.set_ylim(0, 110)
for i, v in enumerate(susceptibility):
    ax.text(i, v + 2, str(v) + "%", ha='center', fontweight='bold')
plt.tight_layout()
plt.savefig('/Users/apple/.gemini/antigravity/brain/c032c25f-f969-42a6-a26a-bbae3329fb52/fig2.png', dpi=300)
plt.close()
