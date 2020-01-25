# Copyright(c) 2020 Jake Fowler
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation
# files (the "Software"), to deal in the Software without
# restriction, including without limitation the rights to use, 
# copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following
# conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, 
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
# OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, 
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.

import pandas as pd
import clr
import System as dotnet
from cmdty_storage import utils
from collections import namedtuple
from pathlib import Path
clr.AddReference(str(Path('cmdty_storage/lib/Cmdty.Storage')))
from Cmdty.Storage import IntrinsicStorageValuation, IIntrinsicAddStartingInventory, \
    IIntrinsicAddCurrentPeriod, IIntrinsicAddForwardCurve, \
    IIntrinsicAddCmdtySettlementRule, IIntrinsicAddDiscountFactorFunc, \
    IIntrinsicAddNumericalTolerance, IIntrinsicCalculate, IntrinsicStorageValuationExtensions
clr.AddReference(str(Path("cmdty_storage/lib/Cmdty.TimePeriodValueTypes")))
import Cmdty.TimePeriodValueTypes as tp


IntrinsicValuationResults = namedtuple('IntrinsicValuationResults', 'npv, profile')


def intrinsic_value(cmdty_storage, val_date, inventory, forward_curve, interest_rates, settlement_rule, 
                    num_inventory_grid_points=100, numerical_tolerance=1E-12):
    """
    Calculates the intrinsic value of commodity storage.

    Args:
        settlement_rule (callable): Mapping function from pandas.Period type to the date on which the cmdty delivered in
            this period is settled. The pandas.Period parameter will have freq equal to the cmdty_storage parameter's freq property.
    """
    if cmdty_storage.freq != forward_curve.index.freqstr:
        raise ValueError("cmdty_storage and forward_curve have different frequencies.")
    time_period_type = utils.FREQ_TO_PERIOD_TYPE[cmdty_storage.freq]

    intrinsic_calc = IntrinsicStorageValuation[time_period_type].ForStorage(cmdty_storage.net_storage)

    IIntrinsicAddStartingInventory[time_period_type](intrinsic_calc).WithStartingInventory(inventory)

    current_period = utils.from_datetime_like(val_date, time_period_type)
    IIntrinsicAddCurrentPeriod[time_period_type](intrinsic_calc).ForCurrentPeriod(current_period)

    net_forward_curve = utils.series_to_double_time_series(forward_curve, time_period_type)
    IIntrinsicAddForwardCurve[time_period_type](intrinsic_calc).WithForwardCurve(net_forward_curve)

    def wrapper_settle_function(py_function, net_time_period, freq):
        pandas_period = utils.net_time_period_to_pandas_period(net_time_period, freq)
        py_function_result = py_function(pandas_period)
        net_settle_day = utils.from_datetime_like(py_function_result, tp.Day)
        return net_settle_day

    def wrapped_function(net_time_period):
        return wrapper_settle_function(settlement_rule, net_time_period, cmdty_storage.freq)

    net_settlement_rule = dotnet.Func[time_period_type, tp.Day](wrapped_function)
    IIntrinsicAddCmdtySettlementRule[time_period_type](intrinsic_calc).WithCmdtySettlementRule(net_settlement_rule)
    
    interest_rate_time_series = utils.series_to_double_time_series(interest_rates, utils.FREQ_TO_PERIOD_TYPE['D'])
    IntrinsicStorageValuationExtensions.WithAct365ContinuouslyCompoundedInterestRateCurve[time_period_type](intrinsic_calc, interest_rate_time_series)

    IntrinsicStorageValuationExtensions.WithFixedNumberOfPointsOnGlobalInventoryRange[time_period_type](intrinsic_calc, num_inventory_grid_points)

    IntrinsicStorageValuationExtensions.WithLinearInventorySpaceInterpolation[time_period_type](intrinsic_calc)

    IIntrinsicAddNumericalTolerance[time_period_type](intrinsic_calc).WithNumericalTolerance(numerical_tolerance)

    net_val_results = IIntrinsicCalculate[time_period_type](intrinsic_calc).Calculate()

    net_profile = net_val_results.StorageProfile
    if net_profile.Count == 0:
        index = pd.PeriodIndex(data=[], freq=cmdty_storage.freq)
    else:
        profile_start = utils.net_datetime_to_py_datetime(net_profile.Indices[0].Start)
        index = pd.period_range(start=profile_start, freq=cmdty_storage.freq, periods=net_profile.Count)

    inventories = [None] * net_profile.Count
    inject_withdraw_volumes = [None] * net_profile.Count
    cmdty_consumed = [None] * net_profile.Count
    inventory_loss = [None] * net_profile.Count
    net_position = [None] * net_profile.Count

    for i, profile_data in enumerate(net_profile.Data):
        inventories[i] = profile_data.Inventory
        inject_withdraw_volumes[i] = profile_data.InjectWithdrawVolume
        cmdty_consumed[i] = profile_data.CmdtyConsumed
        inventory_loss[i] = profile_data.InventoryLoss
        net_position[i] = profile_data.NetPosition

    data_frame_data = {'inventory' : inventories, 'inject_withdraw_volume' : inject_withdraw_volumes,
                  'cmdty_consumed' : cmdty_consumed, 'inventory_loss' : inventory_loss, 'net_position' : net_position}
    data_frame = pd.DataFrame(data=data_frame_data, index=index)
    
    return IntrinsicValuationResults(net_val_results.NetPresentValue, data_frame)