import os
import sys
import pickle
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

# Ensure src/ is on sys.path for bare imports
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# Import fallback GaussianHMM if hmmlearn is not installed
try:
    from hmmlearn.hmm import GaussianHMM
    HMMLEARN_AVAILABLE = True
except ImportError:
    HMMLEARN_AVAILABLE = False

    def multivariate_normal_pdf(X, mean, cov):
        # X: shape (n_samples, n_features)
        # mean: shape (n_features,)
        # cov: shape (n_features, n_features)
        n_samples, n_features = X.shape
        diff = X - mean
        
        # Add regularization to prevent singular matrix
        cov_reg = cov + np.eye(n_features) * 1e-6
        try:
            inv_cov = np.linalg.inv(cov_reg)
            det_cov = np.linalg.det(cov_reg)
        except np.linalg.LinAlgError:
            cov_reg = cov + np.eye(n_features) * 1e-3
            inv_cov = np.linalg.inv(cov_reg)
            det_cov = np.linalg.det(cov_reg)
            
        if det_cov <= 0:
            det_cov = 1e-100
            
        product = np.dot(diff, inv_cov)
        exponent = -0.5 * np.sum(product * diff, axis=1)
        norm_const = 1.0 / np.sqrt(((2 * np.pi) ** n_features) * det_cov)
        return norm_const * np.exp(exponent)

    class GaussianHMM:
        def __init__(self, n_components=3, covariance_type="full", n_iter=100, random_state=None):
            self.n_components = n_components
            self.covariance_type = covariance_type
            self.n_iter = n_iter
            self.random_state = random_state
            self.pi = None
            self.transmat = None
            self.means = None
            self.covars = None
            
        def fit(self, X):
            n_samples, n_features = X.shape
            np.random.seed(self.random_state if self.random_state is not None else 42)
            
            # Initial state distribution
            self.pi = np.full(self.n_components, 1.0 / self.n_components)
            # Transition matrix
            self.transmat = np.full((self.n_components, self.n_components), 1.0 / self.n_components)
            
            # Partition data to initialize means/covars
            indices = np.linspace(0, n_samples, self.n_components + 1, dtype=int)
            self.means = np.zeros((self.n_components, n_features))
            self.covars = np.zeros((self.n_components, n_features, n_features))
            for i in range(self.n_components):
                chunk = X[indices[i]:indices[i+1]]
                self.means[i] = np.mean(chunk, axis=0)
                self.covars[i] = np.cov(chunk, rowvar=False) + np.eye(n_features) * 1e-4
                
            # EM algorithm loop
            for iteration in range(self.n_iter):
                # Compute emission densities
                obs_lik = np.zeros((n_samples, self.n_components))
                for i in range(self.n_components):
                    obs_lik[:, i] = multivariate_normal_pdf(X, mean=self.means[i], cov=self.covars[i])
                obs_lik = np.maximum(obs_lik, 1e-100)
                
                # Forward pass
                alpha = np.zeros((n_samples, self.n_components))
                scale = np.zeros(n_samples)
                alpha[0] = self.pi * obs_lik[0]
                scale[0] = np.sum(alpha[0])
                alpha[0] /= (scale[0] + 1e-100)
                
                for t in range(1, n_samples):
                    alpha[t] = np.dot(alpha[t-1], self.transmat) * obs_lik[t]
                    scale[t] = np.sum(alpha[t])
                    alpha[t] /= (scale[t] + 1e-100)
                    
                # Backward pass
                beta = np.zeros((n_samples, self.n_components))
                beta[-1] = 1.0
                for t in range(n_samples - 2, -1, -1):
                    beta[t] = np.dot(self.transmat, beta[t+1] * obs_lik[t+1])
                    beta[t] /= (scale[t+1] + 1e-100)
                    
                # Gammas (state posteriors)
                gamma = alpha * beta
                gamma /= (np.sum(gamma, axis=1, keepdims=True) + 1e-100)
                
                # Xis (transition posteriors)
                xi = np.zeros((n_samples - 1, self.n_components, self.n_components))
                for t in range(n_samples - 1):
                    numerator = self.transmat * np.outer(alpha[t], obs_lik[t+1] * beta[t+1])
                    xi[t] = numerator / (np.sum(numerator) + 1e-100)
                    
                # Parameter updates
                self.pi = gamma[0] / (np.sum(gamma[0]) + 1e-100)
                
                self.transmat = np.sum(xi, axis=0) / (np.sum(gamma[:-1], axis=0)[:, np.newaxis] + 1e-100)
                self.transmat /= (np.sum(self.transmat, axis=1, keepdims=True) + 1e-100)
                
                for i in range(self.n_components):
                    gamma_i = gamma[:, i]
                    sum_gamma_i = np.sum(gamma_i)
                    self.means[i] = np.sum(X * gamma_i[:, np.newaxis], axis=0) / (sum_gamma_i + 1e-100)
                    diff = X - self.means[i]
                    self.covars[i] = np.dot(diff.T, diff * gamma_i[:, np.newaxis]) / (sum_gamma_i + 1e-100)
                    self.covars[i] += np.eye(n_features) * 1e-5
                    
        def predict(self, X):
            n_samples, n_features = X.shape
            obs_lik = np.zeros((n_samples, self.n_components))
            for i in range(self.n_components):
                obs_lik[:, i] = multivariate_normal_pdf(X, mean=self.means[i], cov=self.covars[i])
            obs_lik = np.maximum(obs_lik, 1e-100)
            
            log_pi = np.log(self.pi + 1e-100)
            log_transmat = np.log(self.transmat + 1e-100)
            log_obs_lik = np.log(obs_lik + 1e-100)
            
            viterbi = np.zeros((n_samples, self.n_components))
            backpointer = np.zeros((n_samples, self.n_components), dtype=int)
            
            viterbi[0] = log_pi + log_obs_lik[0]
            
            for t in range(1, n_samples):
                for j in range(self.n_components):
                    temp = viterbi[t-1] + log_transmat[:, j]
                    backpointer[t, j] = np.argmax(temp)
                    viterbi[t, j] = np.max(temp) + log_obs_lik[t, j]
                    
            states = np.zeros(n_samples, dtype=int)
            states[-1] = np.argmax(viterbi[-1])
            for t in range(n_samples - 2, -1, -1):
                states[t] = backpointer[t+1, states[t+1]]
                
            return states

class RollingHMM:
    def __init__(self, n_components=3, model_dir="data"):
        self.n_components = n_components
        self.model_dir = model_dir
        self.model_path = os.path.join(model_dir, "hmm_model.pkl")
        os.makedirs(model_dir, exist_ok=True)
        
    def prepare_features(self, spy_df, vix_df):
        spy_df = spy_df.copy()
        vix_df = vix_df.copy()
        spy_df.index = spy_df.index.tz_localize(None)
        vix_df.index = vix_df.index.tz_localize(None)

        spy_close_col = "CLOSE" if "CLOSE" in spy_df.columns else "Close"
        vix_close_col = "CLOSE" if "CLOSE" in vix_df.columns else "Close"
        
        df = pd.DataFrame(index=spy_df.index)
        df['spy_close'] = spy_df[spy_close_col]
        df = df.join(vix_df[[vix_close_col]].rename(columns={vix_close_col: 'vix_close'}), how='inner')
        df = df.dropna()
        
        # 1. spy_return_20d: 20-day log return
        df['spy_return_20d'] = np.log(df['spy_close'] / df['spy_close'].shift(20))
        
        # 2. spy_vol_20d: 20-day annualized volatility
        df['daily_log_ret'] = np.log(df['spy_close'] / df['spy_close'].shift(1))
        df['spy_vol_20d'] = df['daily_log_ret'].rolling(20).std() * np.sqrt(252)
        
        # 3. vix_norm: VIX / 100
        df['vix_norm'] = df['vix_close'] / 100.0
        
        # 4. breadth_20d: fraction of positive return days over 20 days
        df['pos_day'] = (df['daily_log_ret'] > 0).astype(float)
        df['breadth_20d'] = df['pos_day'].rolling(20).mean()
        
        df = df.dropna()
        return df[['spy_return_20d', 'spy_vol_20d', 'vix_norm', 'breadth_20d']], df
        
    def fit_model(self, X):
        model = GaussianHMM(n_components=self.n_components, covariance_type="full", n_iter=100, random_state=42)
        model.fit(X)
        return model
        
    def get_regime_label(self, model, last_120_features):
        states = model.predict(last_120_features)
        last_state = states[-1]
        
        means = getattr(model, "means_", None)
        if means is None:
            means = getattr(model, "means")
            
        means_return = means[:, 0]
        sorted_states = np.argsort(means_return) # [bear_idx, sideways_idx, bull_idx]
        
        if last_state == sorted_states[0]:
            return "bear"
        elif last_state == sorted_states[1]:
            return "sideways"
        else:
            return "bull"
            
    def get_regime(self, force_refit=False):
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=1500) # ~4 years
        
        spy_ticker = yf.Ticker("SPY")
        vix_ticker = yf.Ticker("^VIX")
        
        spy_df = spy_ticker.history(start=start_date.isoformat(), end=end_date.isoformat(), interval="1d")
        vix_df = vix_ticker.history(start=start_date.isoformat(), end=end_date.isoformat(), interval="1d")
        
        if spy_df.empty or vix_df.empty:
            print("[HMM WARNING] Failed to download data for regime estimation. Defaulting to bull.")
            return "bull"
            
        features_df, raw_df = self.prepare_features(spy_df, vix_df)
        X = features_df.values
        
        refit_needed = force_refit or not os.path.exists(self.model_path)
        
        if not refit_needed:
            try:
                with open(self.model_path, 'rb') as f:
                    cache_data = pickle.load(f)
                model = cache_data['model']
                last_fit_len = cache_data['fit_len']
                
                # Check 63 days rollout
                if len(X) - last_fit_len >= 63:
                    refit_needed = True
                    print(f"[HMM] Refitting triggered: new data rows {len(X)} - cache fit rows {last_fit_len} >= 63")
                else:
                    # Successfully loaded cached model
                    pass
            except Exception as e:
                print(f"[HMM WARNING] Failed to load cached model: {e}. Refitting.")
                refit_needed = True
                
        if refit_needed:
            print(f"[HMM] Refitting Gaussian HMM model on all history ({len(X)} rows)...")
            model = self.fit_model(X)
            try:
                with open(self.model_path, 'wb') as f:
                    pickle.dump({'model': model, 'fit_len': len(X)}, f)
            except Exception as e:
                print(f"[HMM WARNING] Failed to cache HMM model: {e}")
                
        # Decode Viterbi on the last 120 days
        last_120_X = X[-120:]
        if len(last_120_X) < 120:
            last_120_X = X
            
        regime = self.get_regime_label(model, last_120_X)
        return regime

if __name__ == '__main__':
    print("Testing Rolling HMM regime classifier...")
    hmm = RollingHMM()
    label = hmm.get_regime()
    print("Regime detected:", label.upper())
