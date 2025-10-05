import numpy as np 
import random
from tqdm import tqdm
from copy import deepcopy as dp

class LLMEconEnv:
    def __init__(self, args, players) -> None:
        # this args is from configs/env.yaml
        self.args = args
        self.players = players
        self.n_agent = len(self.players)
        self.tax_trans = np.asarray(self.args.tax_trans)

    def seed_everything(self, seed):
        np.random.seed(seed)
        random.seed(seed)
    
    def _gen_obs(self):
        # generate
        observations = {}
        for i,player in enumerate(self.players):
            observations[player.ID] = {}
            # shared information
            observations[player.ID]["n_step"] = self.ep
            observations[player.ID]["agg_shock"] = np.exp(self.log_z)
            observations[player.ID]["interest_rate"] = np.exp(self.log_ir)
            observations[player.ID]["loan_interest_rate"] = self.average_loan_ir
            observations[player.ID]["save_interest_rate"] = self.r
            observations[player.ID]["wage_rate"] = self.w
            observations[player.ID]["tax_rate"] = self.tax_rate
            # private information
            observations[player.ID]["cash"] = self.cash[i]
            observations[player.ID]["labor"] = self.labor[i]
            observations[player.ID]["utility"] = self.utility[i]
            observations[player.ID]["debt"] = self.debt[i]
        # send
        for player in self.players:
            player.recv_obs(observations[player.ID])

    def reset(self):
        self.cash = np.ones(self.n_agent) * self.args.init_a
        self.labor = np.e**np.random.normal(loc=0,scale=self.args.nu_l,size=self.n_agent)
        self.z = np.e**np.random.normal(loc=0,scale=self.args.nu_z,size=self.n_agent)
        self.average_loan_ir = np.nan
        k = self.cash.mean()
        self.r = self.z * self.args.alpha * k**(self.args.alpha-1) - self.args.delta
        self.w = self.z * (1-self.args.alpha) * k**self.args.alpha
        self.tax_rate = random.choice(self.args.tax_rates)
        self.debt = [[]]*self.n_agent # (player_idx,dir,amount,remain_term)
        self.utility = np.array([np.nan]*self.n_agent)
        self.ep = 0
        self.csmp = np.nan
        self.logs = []
        self._gen_obs()

    def simulate_one_step(self):
        # phase 1: communicate freely
        mapping = {}
        for player in self.players:
            player_idx = np.random.choice(self.n_agent)
            mapping[player.ID] = player_idx
            if player_idx == player.ID: continue # cannot trade with itself
            for info_i in range(self.args.n_info_exchange):
                remain = self.args.n_info_exchange-info_i-1
                inform = player.send_information(remain=remain)
                self.players[player_idx].recv_information(inform,remain=remain)
                inform = self.players[player_idx].send_information(remain=remain)
                player.recv_information(inform,remain=remain)
        # phase 2: make decision
        wealth = self.cash * (1+self.r) + self.w * self.labor * (1-self.tax_rate)
        wealth += np.sum(self.w*self.labor*self.tax_rate)/self.n_agent
        loan_decisions = {}
        self.csmp = 0
        for i,player in enumerate(self.players):
            decision = player.make_decision()
            # consumption
            c = decision["consumption_share"]
            csmp = np.clip(wealth[i] * c, 0.001, wealth[i] - 0.001)
            self.csmp += csmp
            u = np.log(csmp) if csmp > 0 else -np.inf
            self.utility[i] = u
            self.cash[i] = wealth[i] - csmp
            # loan: pre save
            if decision["trade"] == True:
                loan_decisions[player.ID] = player_idx, decision["contract"]
        self.csmp /= self.n_agent
        # filter loans
        loan_irs = []
        for player_idx1, (player_idx2,contract) in loan_decisions.items():
            if not player_idx2 in loan_decisions: continue
            player2_contract = loan_decisions[player_idx2]
            dir2,amount2,ir2,term2 = player2_contract
            dir1,amount1,ir1,term1 = contract
            if dir1 == dir2 or amount1!=amount2 or ir1!=ir2 or term1!=term2: continue
            if dir1 == "lend_out" and amount1 > self.cash[player_idx1]: continue
            if dir2 == "lend_out" and amount2 > self.cash[player_idx2]: continue
            q = amount1 * (ir1*(1+ir1)**term1) / ((1+ir1)**term1 -1)
            loan_irs.append(ir1)
            self.debt[player_idx1].append(
                (dir1,q,term1)
            )
            if dir1 == "borrow_in":
                self.cash[i] += amount1
            elif dir1 == "lend_out":
                self.cash[i] -= amount1
        self.average_loan_ir = np.mean(loan_irs)
        # phase 3: update stochastic process
        self.labor = np.e ** (
            self.args.rho_l * np.log(self.labor) + (1-self.args.rho_l**2)**0.5 * self.args.nu_l * np.random.normal(size=self.n_agent)
        )
        self.z = np.e ** (
            self.args.rho_z * np.log(self.z) + (1-self.args.rho_z**2)**0.5 * self.args.nu_z * np.random.normal()
        )
        k = self.cash.mean()
        self.r = self.z * self.args.alpha * k**(self.args.alpha-1) - self.args.delta
        self.w = self.z * (1-self.args.alpha) * k**self.args.alpha
        current_tax_idx = np.argmin(np.abs(np.asarray(self.args.tax_rates) - self.tax_rate))
        self.tax_rate = self.args.tax_rates[np.random.choice(a=len(self.args.tax_rates),p=self.tax_trans[current_tax_idx])]
        # phase 4: execute loans
        for player in self.players:
            debts = self.debt[player.ID]
            new_debts = []
            for debt in debts:
                direction,quantity,remain_term = debt
                if direction == "borrow_in": self.cash[player.ID] -= quantity
                elif direction == "lend_out": self.cash[player.ID] += quantity
                remain_term -= 1
                if remain_term>0:
                    new_debts.append(
                        (direction,quantity,remain_term)
                    )
            self.debt[player.ID] = new_debts
            if self.cash[player.ID]<0: self.utility[player.ID] -= 100
        # phase 5: update observation
        self._gen_obs()

    def run_simulation(self):
        for seed in range(self.args.n_envs):
            print(f"SEED={seed}")
            self.seed_everything(seed)
            self.reset()
            for _ in tqdm(range(self.args.max_eps)):
                self.record_information()
                self.simulate_one_step()
            self.record_information()
    
    def record_information(self,):
        self.logs.append({
            "ep":dp(self.ep),"r":dp(self.r),"w":dp(self.w),"loan_ir":dp(self.average_loan_ir),"z":dp(self.z),"tax":dp(self.tax_rate),
            "u":dp(self.utility), "agg_csmp":dp(self.csmp), "cash": dp(self.cash)
        })