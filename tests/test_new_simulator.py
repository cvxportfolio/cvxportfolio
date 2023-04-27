# Copyright 2023- The Cvxportfolio Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import cvxpy as cvx
import numpy as np
import pandas as pd
import pytest

from cvxportfolio.simulator import MarketSimulator
from cvxportfolio.estimator import DataEstimator

import cvxportfolio as cp


def test_simulator_raises():

    with pytest.raises(SyntaxError):
        simulator = MarketSimulator()

    with pytest.raises(SyntaxError):
        simulator = MarketSimulator(returns=pd.DataFrame([[0.]]))

    with pytest.raises(SyntaxError):
        simulator = MarketSimulator(volumes=pd.DataFrame([[0.]]))

    with pytest.raises(SyntaxError):
        simulator = MarketSimulator(returns=pd.DataFrame(
            [[0.]]), volumes=pd.DataFrame([[0.]]))

    # not raises
    simulator = MarketSimulator(returns=pd.DataFrame([[0., 0.]]), volumes=pd.DataFrame(
        [[0.]]), per_share_fixed_cost=0., round_trades=False)

    with pytest.raises(SyntaxError):
        simulator = MarketSimulator(returns=pd.DataFrame(
            [[0., 0.]]), volumes=pd.DataFrame([[0.]]), per_share_fixed_cost=0.)

    with pytest.raises(SyntaxError):
        simulator = MarketSimulator(returns=pd.DataFrame(
            [[0., 0.]]), volumes=pd.DataFrame([[0.]]), round_trades=False)


def test_prepare_data(tmp_path):
    simulator = MarketSimulator(['ZM', 'META'], base_location=tmp_path)
    assert simulator.returns.data.shape[1] == 3
    assert simulator.prices.data.shape[1] == 2
    assert simulator.volumes.data.shape[1] == 2
    assert simulator.sigma_estimate.data.shape[1] == 2
    assert np.isnan(simulator.returns.data.iloc[-1, 0])
    assert np.isnan(simulator.volumes.data.iloc[-1, 1])
    assert not np.isnan(simulator.prices.data.iloc[-1, 0])
    assert simulator.returns.data.index[-1] == simulator.volumes.data.index[-1]
    assert simulator.returns.data.index[-1] == simulator.prices.data.index[-1]
    assert simulator.sigma_estimate.data.index[-1] == simulator.prices.data.index[-1]
    assert np.isclose(simulator.sigma_estimate.data.iloc[-1,0],
         simulator.returns.data.iloc[-1001:-1,0].std())
         
def test_methods(tmp_path):
    simulator = MarketSimulator(['ZM', 'META', 'AAPL'], base_location=tmp_path)
    
    for t in [pd.Timestamp('2023-04-13')]:#, pd.Timestamp('2022-04-11')]: # can't because sigma requires 1000 days
        super(simulator.__class__, simulator).values_in_time(t, None, None, None, None)
    
        ## round trade
    
        for i in range(10):
            np.random.seed(i)
            tmp = np.random.uniform(size=4)*1000
            tmp[3] = -sum(tmp[:3])
            u = pd.Series(tmp, simulator.returns.data.columns)
            rounded = simulator.round_trade_vector(u)
            assert sum(rounded) == 0
            assert np.linalg.norm(rounded[:-1] - u[:-1]) < \
                np.linalg.norm(simulator.prices.data.loc[t]/2)
        
            print(u)
        
        old_spread = simulator.spreads
    
        ## transaction cost
    
        for i in range(10):
            np.random.seed(i)
            tmp = np.random.uniform(size=4)*1000
            tmp[3] = -sum(tmp[:3])
            u = simulator.round_trade_vector(u)
        
            simulator.spreads = DataEstimator(np.random.uniform(size=3) * 1E-3)
            simulator.spreads.pre_evaluation(None, None, None, None)
            simulator.spreads.values_in_time(t, None, None, None, None)
        
            shares = sum(np.abs(u[:-1] / simulator.prices.data.loc[t]))
            tcost = - simulator.per_share_fixed_cost * shares
            tcost -= np.abs(u[:-1]) @ simulator.spreads.data / 2
            tcost -= sum((np.abs(u[:-1])**1.5) * simulator.sigma_estimate.data.loc[t] / np.sqrt(simulator.volumes.data.loc[t]))
            sim_tcost = simulator.transaction_costs(u)
        
            assert np.isclose(tcost, sim_tcost)
        
        simulator.spreads = old_spread 
    
        old_dividends = simulator.dividends  
    
        ## stock & cash holding cost
        for i in range(10):
            np.random.seed(i)
            h = np.random.randn(4)*10000
            h[3] = 10000 - sum(h[:3])
        
            simulator.dividends = DataEstimator(np.random.uniform(size=3) * 1E-4)
            simulator.dividends.pre_evaluation(None, None, None, None)
            simulator.dividends.values_in_time(t, None, None, None, None)
        
            sim_hcost = simulator.stocks_holding_costs(h)
        
            cash_return = simulator.returns.data.loc[t][-1]
            total_borrow_cost = cash_return + (0.005)/252
            hcost = -total_borrow_cost * sum(-np.minimum(h,0.)[:3])
            hcost += simulator.dividends.data @ h[:-1]
                
            assert np.isclose(hcost, sim_hcost)
        
            sim_cash_hcost = simulator.cash_holding_cost(h)
        
            real_cash_position = h[3] + sum(np.minimum(h[:-1],0.))
            if real_cash_position > 0:
                cash_hcost = real_cash_position * (cash_return - 0.005/252)
            if real_cash_position < 0:
                cash_hcost = real_cash_position * (cash_return + 0.005/252)
                
            assert np.isclose(cash_hcost, sim_cash_hcost)
        
        simulator.dividends = old_dividends 
        
def test_simulate_policy(tmp_path):
    simulator = MarketSimulator(['META', 'AAPL'], base_location=tmp_path)
    

    start_time = '2023-03-10'
    end_time = '2023-04-20'
    
    ## hold
    policy = cp.Hold()
    for i in range(10):
        np.random.seed(i)
        h = np.random.randn(3)*10000
        h[-1] = 10000 - sum(h[:-1])
        h0 = pd.Series(h, simulator.returns.data.columns)
        h = pd.Series(h0, copy=True)
        simulator.initialize_policy(policy, start_time, end_time)
        for t in simulator.returns.data.index[(simulator.returns.data.index >= start_time) & (simulator.returns.data.index <= end_time)]:
            oldcash = h[-1]
            h, z, u, tcost, stock_hcost, cash_hcost = simulator.simulate(t=t, h=h, policy=policy)
            assert tcost == 0.
            if np.all(h0[:2] > 0):
                assert stock_hcost == 0.
            assert np.isclose(oldcash + stock_hcost + cash_hcost, h[-1])
            
        simh = h0[:-1] * simulator.prices.data.loc[pd.Timestamp(end_time) + pd.Timedelta('1d')] / simulator.prices.data.loc[start_time]
        assert np.allclose(simh, h[:-1])
        
    ## proportional_trade
    policy = cp.ProportionalTradeToTargets(
    targets = pd.DataFrame({pd.Timestamp(end_time) + pd.Timedelta('1d'):  pd.Series([0, 0, 1], simulator.returns.data.columns)}).T)
        
    for i in range(10):
        np.random.seed(i)
        h = np.random.randn(3)*10000
        h[-1] = 10000 - sum(h[:-1])
        h0 = pd.Series(h, simulator.returns.data.columns)
        h = pd.Series(h0, copy=True)
        simulator.initialize_policy(policy, start_time, end_time)
        for t in simulator.returns.data.index[(simulator.returns.data.index >= start_time) & (simulator.returns.data.index <= end_time)]:
            oldcash = h[-1]
            h, z, u, tcost, stock_hcost, cash_hcost = simulator.simulate(t=t, h=h, policy=policy)
            print(h)
            print(tcost, stock_hcost, cash_hcost)
            
        assert np.all(np.abs(h[:-1]) < simulator.prices.data.loc[end_time])
          
            
    