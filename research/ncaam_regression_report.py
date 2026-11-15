"""Render the completed offline weight study; never writes model selections."""
from __future__ import annotations

import argparse
from datetime import date, datetime
from html import escape
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image

from research.ncaam_matchup_regression import Fit, Model, design_eff, design_pace, prepare
from builders.ncaam_matchup_projection import from_captured_inputs

NAVY='#12243B';TEAL='#087F8C';VIOLET='#7450A0';GRAY='#526170';LIGHT='#EDF3F7';AMBER='#A46A13'


def restored_model(data):
    e,p=data['efficiency'],data['pace']
    return Model(data['family'],data['penalty'],data['nonnegative'],
        Fit(np.array(list(e.values())),list(e)),Fit(np.array(list(p.values())),list(p)))


def example_frames(f,opening_day):
    day=(date.fromisoformat(f['tipoff'][:10])-date.fromisoformat(opening_day)).days
    g={'game_id':f['gameId'],'season':2027,'date':f['tipoff'][:10],'ratings_date':f['ratings_observed_at'][:10],
       'day':day,'stage':min(day//7,3),'overtime':False,'neutral':f['neutral_site']}
    for m,b in f['benchmark']['metrics'].items():
        g['mean_'+m]=b['mean'];g['sd_'+m]=b['sd']
        for s in ('away','home'):
            g[s+'_'+m]=f[s+'_rating'][m];g[s+'_'+m+'_z']=(g[s+'_'+m]-b['mean'])/b['sd']
    rows=[]
    for s,o,v in (('away','home',-1.),('home','away',1.)):
        r={k:g[k] for k in ('game_id','season','date','ratings_date','day','stage','overtime','neutral')}
        r.update(team=f[s+'_rating']['team'].lower().replace('.',''),opponent=f[o+'_rating']['team'].lower().replace('.',''),
                 side=s,venue=0. if f['neutral_site'] else v,center=(g['mean_offense']+g['mean_defense'])/2)
        for m in ('offense','defense','tempo'):
            r['mean_'+m]=g['mean_'+m];r['sd_'+m]=g['sd_'+m]
            r['own_'+m]=g[s+'_'+m];r['opp_'+m]=g[o+'_'+m]
            r['own_'+m+'_z']=g[s+'_'+m+'_z'];r['opp_'+m+'_z']=g[o+'_'+m+'_z']
        rows.append(r)
    return pd.DataFrame([g]),pd.DataFrame(rows)


def example_projection(f,model,opening_day,team_lookup=None):
    g,t=example_frames(f,opening_day);ex,_=design_eff(t,model.family);px,_=design_pace(g,model.family)
    pace=float(g.mean_tempo.iloc[0]+model.pace.predict(px)[0]);pp100=t.center.to_numpy()+model.eff.predict(ex)
    # Replace the generic fitted venue term with the example's captured court,
    # then apply its saved player deltas once. These do not change total venue.
    venue=model.eff.coefficients[model.eff.columns.index('venue')]
    pp100-=t.venue.to_numpy()*venue
    if team_lookup:
        for side in ('away','home'):
            key=f[side+'_rating']['team'].lower().replace('.','');a=team_lookup.get(key,{})
            pace+=a.get('pace_intercept',0)+a.get('tempo_slope_adjustment',0)*(f[side+'_rating']['tempo']-g.mean_tempo.iloc[0])
        for i,(s,o) in enumerate((('away','home'),('home','away'))):
            a=team_lookup.get(t.team.iloc[i],{});b=team_lookup.get(t.opponent.iloc[i],{})
            pp100[i]+=a.get('efficiency_intercept',0)+a.get('offense_slope_adjustment',0)*(f[s+'_rating']['offense']-g.mean_offense.iloc[0])+b.get('defense_slope_adjustment',0)*(f[o+'_rating']['defense']-g.mean_defense.iloc[0])
    points={}
    for i,(s,o) in enumerate((('away','home'),('home','away'))):
        delta=f['lineup_adjustment'][s]['offense_change_per_100']-f['lineup_adjustment'][o]['defense_improvement_per_100']
        points[s]=pace*(pp100[i]+delta)/100+f['home_court_points']/2*(1 if s=='home' else -1)
    return {'away':float(points['away']),'home':float(points['home']),'total':float(sum(points.values())),'pace':pace}


def create_examples(report,dataset,examples_path):
    opening=min(g['date'] for g in dataset['games'] if g['season']==2027)
    linear=restored_model(report['descriptive_linear_model']);levels=restored_model(report['descriptive_level_model'])
    teams={r['team']:r for r in report['teams']};output=[]
    for f in json.loads(Path(examples_path).read_text())['games']:
        kwargs={k:f[k] for k in ('tipoff','home_court_points','neutral_site','lineup_adjustment')}
        kwargs.update(as_of=f['lockedAt'],source_url=f['ratings_source_url'])
        original=from_captured_inputs(f['away_rating'],f['home_rating'],f['benchmark'],model_version=f['modelVersion'],**kwargs)
        revised=from_captured_inputs(f['away_rating'],f['home_rating'],f['benchmark'],**kwargs)
        def simple(p):return {'away':p['away_points'],'home':p['home_points'],'total':p['total'],'pace':p['possessions']}
        output.append({'game':f['away_team']+' @ '+f['home_team'],'away_name':f['away_rating']['team'],'home_name':f['home_rating']['team'],
            'line':f['market']['total'],'as_of':f['lockedAt'],'models':{
            'Original saved':simple(original),'Pace corrected':simple(revised),
            'Fitted league':example_projection(f,linear,opening),'Level-specific':example_projection(f,levels,opening),
            'Team adjustments':example_projection(f,linear,opening,teams)}})
    return output


def create_charts(report,benchmark,output):
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'axes.unicode_minus':False})
    fig,axes=plt.subplots(3,1,figsize=(7.1,8.6),sharex=True)
    factor_colors={'offense':TEAL,'defense':VIOLET,'tempo':'#4674AE'}
    raw_labels={
        'offense':['Very weak','Weak','Below average','Above average','Strong','Very strong'],
        'defense':['Very strong','Strong','Above average','Below average','Weak','Very weak'],
        'tempo':['Very slow','Slow','Below average','Above average','Fast','Very fast']}
    for ax,factor in zip(axes,('offense','defense','tempo')):
        rows=[r for r in report['level_weights'] if r['factor']==factor];pos=np.arange(6)
        w=np.array([r['weight'] for r in rows]);lo=np.array([r['low95'] for r in rows]);hi=np.array([r['high95'] for r in rows])
        ax.axvline(1,color=GRAY,ls='--',lw=1);ax.errorbar(w,pos,xerr=np.vstack((w-lo,hi-w)),fmt='o',color=factor_colors[factor],capsize=3,lw=1.4)
        ax.set_yticks(pos,raw_labels[factor]);ax.invert_yaxis();ax.set_title(factor.title()+' - incremental weight by level',loc='left',fontweight='bold',color=NAVY,pad=8)
        ax.grid(axis='x',color='#DCE5EC',lw=.6);ax.set_axisbelow(True)
        for i,r in enumerate(rows):
            if r['q_current_weight_1']<.05:ax.plot(r['weight'],i,'o',ms=9,mfc='none',mec=AMBER,mew=1.5)
    axes[-1].set_xlabel('Weight per additional rating point within the band (current weight = 1)')
    axes[-1].set_xlim(-.65,2.3);fig.subplots_adjust(left=.24,right=.96,top=.95,bottom=.08,hspace=.5)
    fig.savefig(output/'level_weights.png',dpi=220,facecolor='white');plt.close(fig)
    linear=report['descriptive_linear_model'];rows={r['term']:r for r in report['coefficient_rows'] if r['component']=='efficiency_linear'}
    pace=next(r for r in report['coefficient_rows'] if r['component']=='pace_linear' and r['term']=='tempo')
    center=(benchmark['offense']['mean']+benchmark['defense']['mean'])/2
    scaling=[benchmark['offense']['sd']*benchmark['tempo']['mean']/100,benchmark['defense']['sd']*benchmark['tempo']['mean']/100,benchmark['tempo']['sd']*center/100]
    sources=[rows['offense'],rows['defense'],pace];vals=[r['estimate']*s for r,s in zip(sources,scaling)]
    low=[r['low95']*s for r,s in zip(sources,scaling)];high=[r['high95']*s for r,s in zip(sources,scaling)]
    fig,ax=plt.subplots(figsize=(7.1,2.6));y=np.arange(3)
    ax.barh(y,vals,color=[TEAL,VIOLET,'#4674AE'],height=.52)
    ax.errorbar(vals,y,xerr=np.vstack((np.array(vals)-low,np.array(high)-vals)),fmt='none',color=NAVY,capsize=4)
    ax.set_yticks(y,['Own offense +1 SD','Opposing defense +1 SD weaker',"One team's tempo +1 SD"]);ax.invert_yaxis()
    for i,v in enumerate(vals):ax.text(v+.18,i,f'{v:.2f}',va='center',color=NAVY,fontweight='bold')
    ax.set_xlabel('Predicted change in one team score, points');ax.set_xlim(0,max(high)+.9);ax.grid(axis='x',color='#DFE7ED',lw=.6);ax.set_axisbelow(True)
    fig.subplots_adjust(left=.43,right=.96,bottom=.26,top=.96);fig.savefig(output/'standardized_effects.png',dpi=220,facecolor='white');plt.close(fig)
    return [{'factor':f,'score_effect':float(v),'low95':float(l),'high95':float(h)} for f,v,l,h in zip(('offense','opposing_defense','one_team_tempo'),vals,low,high)]


def build_pdf(report,dataset,examples,effects,output):
    fonts=Path('/usr/share/fonts/truetype/dejavu');pdfmetrics.registerFont(TTFont('DV',str(fonts/'DejaVuSans.ttf')));pdfmetrics.registerFont(TTFont('DV-Bold',str(fonts/'DejaVuSans-Bold.ttf')))
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='BodyX',fontName='DV',fontSize=9.3,leading=13.5,textColor=colors.HexColor(NAVY),spaceAfter=8))
    styles.add(ParagraphStyle(name='SmallX',parent=styles['BodyX'],fontSize=8,leading=11,spaceAfter=6,textColor=colors.HexColor(GRAY)))
    styles.add(ParagraphStyle(name='TitleX',fontName='DV-Bold',fontSize=23,leading=28,textColor=colors.HexColor(NAVY),spaceAfter=14))
    styles.add(ParagraphStyle(name='H1X',fontName='DV-Bold',fontSize=16,leading=20,textColor=colors.HexColor(NAVY),spaceAfter=12))
    styles.add(ParagraphStyle(name='H2X',fontName='DV-Bold',fontSize=11.5,leading=15,textColor=colors.HexColor(TEAL),spaceBefore=6,spaceAfter=7))
    styles.add(ParagraphStyle(name='CellX',fontName='DV',fontSize=8.2,leading=11,textColor=colors.HexColor(NAVY)))
    styles.add(ParagraphStyle(name='HeadCellX',parent=styles['CellX'],fontName='DV-Bold',textColor=colors.white))
    def p(s,style='BodyX'):return Paragraph(s,styles[style])
    def table(rows,widths=None):
        values=[[p(escape(str(c)),'HeadCellX' if i==0 else 'CellX') for c in row] for i,row in enumerate(rows)]
        t=Table(values,colWidths=widths or [532/len(rows[0])]*len(rows[0]),repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor(NAVY)),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor(LIGHT),colors.white]),('LINEBELOW',(0,-1),(-1,-1),.5,colors.HexColor('#D4E0E8'))]))
        return t
    story=[]
    story += [p('NCAAM matchup-weight regression','TitleX'),p('EZPZ Picks | November 15, 2026 | Research only','SmallX'),
        p('Different strength levels matter. A universal extra boost is not supported.','H1X'),
        p('The regression estimates offense and opposing defense at close to equal overall weights. Strong defensive bands have a larger estimated marginal effect, while very fast tempo and the weakest offensive tail flatten. Those patterns do not justify multiplying every extreme rating by the same bonus.'),
        table([['Qualified games','Prior-season fit','Current-season check'],['6,316 across six Novembers','5,412 non-overtime games','575 non-overtime games']]),Spacer(1,12),
        p('Forecast accuracy: gains are modest','H2X'),p('Total-score mean absolute error, in points. Lower is better. Every comparison uses ratings dated before the game.'),
        table([['Period','Games','Current fixed','Fitted league','Team weights'],
            ['Validation: Nov 2024','1,169',f"{report['validation']['current_fixed']['total']['mae']:.3f}",f"{report['validation']['linear']['total']['mae']:.3f}",f"{report['team_validation']['1000']['total']['mae']:.3f}"],
            ['Holdout: Nov 2025','1,190',f"{report['test']['current_fixed']['total']['mae']:.3f}",f"{report['test']['selected_league']['total']['mae']:.3f}",f"{report['test']['team_shrunk']['total']['mae']:.3f}"],
            ['External: Nov 1-14, 2026','575',f"{report['current']['current_fixed']['total']['mae']:.3f}",f"{report['current']['selected_league']['total']['mae']:.3f}",f"{report['current']['team_shrunk']['total']['mae']:.3f}"]],[170,55,102,102,103]),Spacer(1,10),
        p('The existing fixed model won validation. The linear model was best among the fitted regressions; team-specific weights had a small conditional validation gain but failed to improve the later checks. No replacement is automatically selected from the later results.'),
        p('The league fit improved holdout total MAE by only 0.050 points (95% moving-block interval: -0.135 to +0.008 for fitted minus fixed). The current-season reduction was 0.102 points. These are calibration leads, not a demonstrated improvement in betting returns.'),
        p('Production status','H2X'),p('The live pace-corrected model remains unchanged by this study. No regression coefficient, historical forecast, official EZPZ Pick or posting rule has been replaced.')]
    story += [PageBreak(),p('Overall weights and importance','H1X')]
    coef={(v['component'],v['term']):v for v in report['coefficient_rows']}
    rows=[['Factor','Weight','95% interval','Meaning']]
    for label,component,term,meaning in [('Own offense','efficiency_linear','offense','Scoring efficiency per rating point'),('Opposing defense','efficiency_linear','defense','Scoring efficiency per defensive rating point'),('Tempo','pace_linear','tempo','Possessions per summed tempo departure')]:
        r=coef[(component,term)];rows.append([label,f"{r['estimate']:.3f}",f"{r['low95']:.3f} to {r['high95']:.3f}",meaning])
    story += [table(rows,[116,66,125,225]),Spacer(1,9),p('Offense and defense are not percentages of a fixed pie. Both are measured in points per 100 possessions; lower defensive ratings mean better defense. Tempo predicts possessions, so its coefficient has different units.'),
        p('The offense point estimate is about 6.5% larger than defense per rating point, but the difference is not conclusive at the 5% level (shared-school robust p = 0.081). All three factor groups are strongly associated with their respective outcomes (p &lt; 0.001).'),
        Image(str(output/'standardized_effects.png'),width=532,height=195),
        p('The chart converts one standard deviation at the original November 15 national benchmark into one team\'s scoring points, with other factors at average. One team\'s tempo also changes the opponent\'s scoring opportunities. These effects are not shares of explained variance.','SmallX'),
        p('What happens when a factor is removed?','H2X'),p('Each ablation is refitted on the training years. These are holdout errors, not just coefficient significance.'),
        table([['Model','Total MAE','Team-score MAE','Margin MAE'],
            ['Full league fit','13.707','8.551','9.989'],
            *[[f'Without {k}',f"{v['metrics']['total']['mae']:.3f}",f"{v['metrics']['team']['mae']:.3f}",f"{v['metrics']['margin']['mae']:.3f}"] for k,v in report['ablations'].items()]],[190,114,114,114]),Spacer(1,8),
        p('Tempo and offense supplied the largest incremental improvement for totals in this sample. Defense still matters: removing it increased team-score error and margin error substantially, even though its incremental total-error benefit was small.')]
    story += [PageBreak(),p('Estimated weights by strength level','H1X'),
        p('These are local slopes from the unpenalized level model. A weight of 1.00 matches the current per-rating-point effect. The model is continuous: a slope applies only to the portion of a rating inside that band, not to the entire rating or team score.'),
        p('Offense and defense','H2X')]
    lookup={(r['factor'],r['level']):r for r in report['level_weights']}
    labels=['Very weak','Weak','Below average','Above average','Strong','Very strong'];levels=['very_low','low','below_average','above_average','high','very_high']
    rows=[['Strength','Offense weight [95% CI]','Defense weight [95% CI]']]
    for i,label in enumerate(labels):
        a=lookup[('offense',levels[i])];b=lookup[('defense',levels[5-i])]
        def cell(r):return f"{r['weight']:.2f}{'*' if r['q_current_weight_1']<.05 else ''} [{r['low95']:.2f}, {r['high95']:.2f}]"
        rows.append([label,cell(a),cell(b)])
    story += [table(rows,[142,195,195]),Spacer(1,10),p('Tempo','H2X')]
    tempo_labels=['Very slow','Slow','Below average','Above average','Fast','Very fast']
    rows=[['Pace level','Weight [95% CI]','Prior-season team-games']]
    counts={(v['factor'],v['level']):v['team_games'] for v in report['level_counts']}
    for i,label in enumerate(tempo_labels):rows.append([label,cell(lookup[('tempo',levels[i])]),f"{counts[('tempo',levels[i])]:,}"])
    story += [table(rows,[142,195,195]),Spacer(1,9),
        p('* Different from weight 1 after correcting the 18 band comparisons for false discoveries (q &lt; 0.05). Intervals shown are pointwise 95% intervals. They include uncertainty from games sharing a school.','SmallX'),
        p('The strong-defense band has an estimated weight of 1.65. Very strong offense is 1.10 with a wide interval, so its extra boost is uncertain. Very fast tempo is about 0.58. At the weakest offensive end, the additional decline flattens instead of becoming stronger.'),
        p('The joint tests detect differences between levels (offense p = 0.0012; defense p &lt; 0.00001; tempo p = 0.00050). However, the level model did not beat the simpler fit on validation. Its current-season MAE is 13.032 versus 13.040 for the linear fit - a very small difference.')]
    story += [PageBreak(),p('How the estimated effects change across the scale','H1X'),
        p('Dots are incremental slopes; bars are 95% shared-school robust intervals. The dashed line is the current weight of 1. Gold rings mark differences that survive the multiple-comparison correction.','SmallX'),
        Image(str(output/'level_weights.png'),width=510,height=618)]
    story += [PageBreak(),p('Replaying the two examples','H1X'),
        p('These are retrospective research calculations at the original pregame inputs, not newly published predictions for games that have already started. The fitted weights use only completed prior seasons; neither game\'s result is used. Captured player and home-court adjustments are applied once.')]
    for ex in examples:
        story += [p(escape(ex['away_name']+' at '+ex['home_name'])+f" | market total {ex['line']:g}",'H2X')]
        rows=[['Calculation',ex['away_name'],ex['home_name'],'Total']]
        for label,m in ex['models'].items():rows.append([label,f"{m['away']:.1f}",f"{m['home']:.1f}",f"{m['total']:.1f}"])
        story += [table(rows,[220,104,104,104]),Spacer(1,12)]
    story += [p('Nebraska\'s scoring strength and defensive strength work in opposite directions for the total. Giving its offense more effect can raise its own score, while its defense lowers Boise\'s score. The appropriate total comes from both scoring matchups and the projected possessions.'),
        p('A slow, weaker-offense matchup can still project above a sufficiently low market total. The regression estimates points; it does not move a forecast across a line to agree with a qualitative label.'),
        p('Team-specific weights','H2X'),p('The experiment fitted strongly shrunk school-specific offense, defense and tempo adjustments for 367 schools. It did not run 367 unrelated regressions. Nebraska had 36 qualifying prior-season team-games in this November-only pool; Boise had 30, Harvard 31 and American 32. These are thin samples for independent level-by-level weights.'),
        p('Team adjustments worsened current-season total MAE from 13.040 to 13.222 and also worsened team-score and margin error. The study does not support enabling school-specific weights now.')]
    story += [PageBreak(),p('Method, checks and practical limits','H1X'),
        p('Chronological design','H2X'),
        table([['Role','Calendar period','Non-overtime games'],['Initial fit','November 2021-2023','3,053'],['Selection only','November 2024','1,169'],['Untouched historical check','November 2025','1,190'],['External current check','November 1-14, 2026','575']],[170,245,117]),Spacer(1,10),
        p('After choosing the regression family and shrinkage on November 2024, the historical check refits on earlier completed years and predicts November 2025. For the current-season check, the frozen specification is refitted on the five completed prior November periods. No current-season outcomes enter those weights.'),
        p('Inputs and outcomes','H2X'),p('The study downloaded 140 dated Torvik national-rating archives. A game uses the prior calendar day\'s archive, including that archive\'s national means and standard deviations. Source-game adjusted efficiency columns are never predictors. Only reciprocal Division I game rows with agreeing scores, venues, minutes and possessions qualify. An additional 173 paired games were outside the dated Division I population.'),
        p('Scoring efficiency is actual points per 100 estimated possessions. Pace is possessions per 40 minutes. Primary fitting and scoring evaluation exclude overtime, avoiding extra minutes masquerading as scoring skill. Home/away and opening-week controls are included; no market line enters a regression.'),
        p('Inference and validation','H2X'),p('Coefficient intervals use a sandwich covariance that accounts for observations sharing either school, including paired scores and repeated matchups. The 18 comparisons to the current unit weights use Benjamini-Hochberg false-discovery correction. Forecast-error intervals use paired circular seven-date moving blocks. These remain approximate intervals from a limited number of November periods.'),
        p('What this establishes','H2X'),p('It estimates conditional predictive weights and tests whether they generalize. It does not establish causal offense-versus-defense effects, betting profitability, an optimal grading threshold, or accuracy outside November. Archived daily inputs can differ from exact intraday builder snapshots; roster and coaching changes limit team-specific persistence.'),
        p('Primary references and reproducibility','H2X'),
        p('Bart Torvik, archived ratings and bulk data: <link href="https://adamcwisports.blogspot.com/p/data.html" color="#087F8C">provider data notes</link>. Archive pattern: barttorvik.com/timemachine/team_results/YYYYMMDD_team_results.json.gz. Game logs: barttorvik.com/getgamestats.php?year=YYYY&amp;csv=1.','SmallX'),
        p('Ken Pomeroy, <link href="https://kenpom.com/blog/ratings-glossary/" color="#087F8C">Ratings Glossary</link>, for efficiency and adjusted-tempo units. Aronow, Samii and Assenova, <link href="https://arxiv.org/abs/1312.3398" color="#087F8C">Cluster-Robust Variance Estimation for Dyadic Data</link>, for shared-member dependence.','SmallX'),
        p('The accompanying data package includes frozen source files, hashes, coefficients, level weights, school adjustments and heldout predictions. Research code is separate from the live builder. All 140 backend checks and 119 subtests passed, including six new data-integrity and regression checks.','SmallX')]
    def page(canvas,doc):
        canvas.saveState();canvas.setStrokeColor(colors.HexColor('#DCE6EC'));canvas.line(40,37,572,37)
        canvas.setFont('DV',8);canvas.setFillColor(colors.HexColor(GRAY));canvas.drawString(40,23,'EZPZ Picks - NCAAM regression research - November 15, 2026');canvas.drawRightString(572,23,str(doc.page));canvas.restoreState()
    path=output/'NCAAM_matchup_weight_study_2026-11-15.pdf'
    SimpleDocTemplate(str(path),pagesize=(612,792),leftMargin=40,rightMargin=40,topMargin=38,bottomMargin=49,title='NCAAM matchup-weight regression',author='EZPZ Picks research').build(story,onFirstPage=page,onLaterPages=page)
    return path


def main(args):
    result_dir=Path(args.results);report=json.loads((result_dir/'results.json').read_text());dataset=json.loads(Path(args.data).read_text())
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    examples=create_examples(report,dataset,args.examples)
    benchmark=json.loads(Path(args.examples).read_text())['games'][0]['benchmark']['metrics']
    effects=create_charts(report,benchmark,output)
    (result_dir/'example_projections.json').write_text(json.dumps(examples,indent=2)+'\n')
    (result_dir/'standardized_effects.json').write_text(json.dumps(effects,indent=2)+'\n')
    pdf=build_pdf(report,dataset,examples,effects,output)
    print(json.dumps({'pdf':str(pdf),'examples':examples,'standardized_effects':effects},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--results',required=True);parser.add_argument('--data',required=True)
    parser.add_argument('--examples',required=True);parser.add_argument('--output',required=True)
    main(parser.parse_args())
