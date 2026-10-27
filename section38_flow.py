"""Render the entire Section 3.8 control flow as a portable PNG/SVG."""
from pathlib import Path
import os
os.environ.setdefault('MPLCONFIGDIR',str(Path(__file__).resolve().parent/'.cache/matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch,Polygon


def draw(output):
    out=Path(output); out.mkdir(exist_ok=True,parents=True)
    fig,ax=plt.subplots(figsize=(12,17)); ax.set(xlim=(0,12),ylim=(-1,18)); ax.axis('off')
    def box(y,title,detail,color='#e3eef7',height=1.35):
        ax.add_patch(FancyBboxPatch((1.5,y-height/2),8,height,boxstyle='round,pad=0.10',facecolor=color,edgecolor='#263d51',lw=1.6))
        ax.text(5.5,y+.26,title,ha='center',va='center',fontsize=12,weight='bold')
        ax.text(5.5,y-.20,detail,ha='center',va='center',fontsize=9.5)
    def arrow(points,label=None):
        for a,b in zip(points[:-2],points[1:-1]): ax.plot([a[0],b[0]],[a[1],b[1]],color='#263d51')
        ax.annotate('',xy=points[-1],xytext=points[-2],arrowprops=dict(arrowstyle='->',color='#263d51',lw=1.5))
        if label: ax.text(points[0][0]+.1,points[0][1],label,fontsize=9,va='bottom')
    def decision(y,text):
        ax.add_patch(Polygon([(3,y),(5.5,y+.65),(8,y),(5.5,y-.65)],facecolor='#fff1cb',edgecolor='#263d51',lw=1.5))
        ax.text(5.5,y,text,ha='center',va='center',fontsize=10)
    box(17,'START — load experiment configuration','Select walkthrough or calibrated research backend',height=.9)
    box(15.5,'PHASE 1 — Model development and baselines','Three IEEE39 proxies • 12 RE baselines • convergence\n10 candidate sites • electrical distance • noise and runtime checks')
    decision(13.9,'Models converge and\nbaselines saved?')
    box(12.3,'PHASE 2 — Reference screening','Enumerate five count/dispersion configurations per grid\nCompare 1%, 2%, 5% thresholds • select reference N and dispersion')
    box(10.3,'PHASE 3 — Replacement-ratio surface','50 cells × 3 seeds × 2,000 research optimizer evaluations\nFixed-site decomposition • MOPSO/AMOSA and finite-grid benchmarks')
    decision(8.6,'Computing budget\nfor spacing?')
    box(6.9,'PHASE 4 — Spacing refinement','Free-capacity sweep • matched total MW and MWh\nLow / Medium / High dispersion • saturation over tested site counts')
    box(4.9,'PHASE 5 — Validation and sensitivity','RMS versus detailed switching model • 5% comparison\nEleven one-seed sensitivity cases • unsupported tests remain unavailable',color='#fff1cb')
    box(2.9,'DATA ANALYSIS','Run minima and ranges • binding groups and handovers\nFailure counts with unknowns • capacity caps 1.0 / 1.25 / 1.5 / 2.0')
    box(.8,'END — retain results and audit','Research completion requires all physical evidence and validation\nA completed walkthrough is not a validated physical study',color='#e4f1e8')
    arrow([(5.5,16.55),(5.5,16.2)])
    arrow([(5.5,14.75),(5.5,14.55)])
    arrow([(5.5,13.25),(5.5,13.05)],'YES')
    arrow([(8,13.9),(10.7,13.9),(10.7,15.5),(9.65,15.5)],'NO: bounded retry')
    ax.text(10.65,14.5,'Stop after\nretry limit',ha='center',fontsize=8)
    arrow([(5.5,11.55),(5.5,11.05)])
    arrow([(5.5,9.55),(5.5,9.25)])
    arrow([(5.5,7.95),(5.5,7.65)],'YES')
    arrow([(8,8.6),(10.5,8.6),(10.5,4.9),(9.65,4.9)],'NO: record limitation')
    arrow([(5.5,6.15),(5.5,5.65)])
    arrow([(5.5,4.15),(5.5,3.65)])
    arrow([(5.5,2.15),(5.5,1.55)])
    ax.set_title('Section 3.8 — Entire Experimental Procedure\nPython / pandapower + MATLAB / Simulink',fontsize=16,pad=16)
    fig.tight_layout(); fig.savefig(out/'section38_flow.png',dpi=170); fig.savefig(out/'section38_flow.svg'); plt.close(fig)


if __name__=='__main__':
    import sys
    draw(sys.argv[1] if len(sys.argv)>1 else 'results_section38')
