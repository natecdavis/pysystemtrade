from sysquant.estimators.vol import robust_vol_calc
from systems.provided.rules.ewmac import ewmac


def mr_wings(price, vol, Lfast=4):
    Lslow = Lfast * 4
    ewmac_signal = ewmac(price, vol, Lfast, Lslow)
    ewmac_std = ewmac_signal.rolling(5000, min_periods=3).std()
    ewmac_signal[ewmac_signal.abs() < ewmac_std * 3] = 0.0
    mr_signal = -ewmac_signal

    return mr_signal


def mr_wings_calc_vol(price, Lfast=4, vol_days=35):
    vol = robust_vol_calc(price.diff(), vol_days)
    return mr_wings(price, vol, Lfast=Lfast)
