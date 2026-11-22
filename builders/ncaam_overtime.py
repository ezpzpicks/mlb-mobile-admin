"""Frozen prior-season expected-overtime allowance for the totals component."""
import math

POLICY = 'ncaam-prior-november-overtime-2026-11-21-v1'
PARAMETERS = {
    'intercept': -2.49259655302214,
    'squared_margin_coefficient': -0.41691748228307984,
    'neutral_coefficient': 0.04107807768514364,
    'margin_scale': 10.,
    'conditional_mean_periods': 1.1993355481727574,
    'training_home_court': 3.2,
    'regulation_minutes': 40.,
    'overtime_minutes': 5.,
    'training_games': 5714,
    'training_through': '2025-11-30',
}


def overtime_allowance(regulation_total, base_home_margin, *, home_court, neutral_site):
    """Estimate expected extra points; no book line or result is an input.

    Occurrence uses dated November 2021-25 ratings. Duration uses 301 agreeing
    whole-period overtime games. Scoring in overtime is assumed to follow the
    regulation scoring rate. This is not a calibrated cover probability.
    """
    if type(neutral_site) is not bool:
        raise ValueError('A verified neutral-site flag is required.')
    values=(regulation_total,base_home_margin,home_court)
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in values):
        raise ValueError('Finite point inputs are required.')
    if regulation_total<=0 or not 0<=home_court<=8 or (neutral_site and home_court!=0):
        raise ValueError('Invalid point or venue inputs.')
    p=PARAMETERS
    margin=base_home_margin-home_court+(0 if neutral_site else p['training_home_court'])
    logit=(p['intercept']+p['squared_margin_coefficient']*(margin/p['margin_scale'])**2
           +p['neutral_coefficient']*int(neutral_site))
    e=math.exp(logit) if logit<0 else math.exp(-logit)
    probability=e/(1+e) if logit<0 else 1/(1+e)
    points=regulation_total*p['overtime_minutes']/p['regulation_minutes']*probability*p['conditional_mean_periods']
    return {'policy':POLICY,'parameters':dict(PARAMETERS),
            'estimated_overtime_probability':probability,'expected_extra_total_points':points,
            'regulation_total':regulation_total,'total_including_expected_overtime':regulation_total+points}
