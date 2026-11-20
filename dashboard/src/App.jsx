import React, { useState, useEffect, useCallback } from 'react';
import { setOnUnauthorized } from './api/client';
import {
  fetchSession,
  fetchAuthConfig,
  loginWithGoogle,
  loginWithDemo,
  onboardUser,
  logoutUser,
} from './api/authApi';
import {
  fetchSummary,
  fetchInventory,
  fetchParties,
  fetchTransactions,
  fetchCandidates,
  confirmCandidate,
  cancelCandidate,
  clarifyCandidate,
  reviewCandidate,
} from './api/dashboardApi';

import Header from './components/layout/Header';
import Footer from './components/layout/Footer';
import AlertBanner from './components/common/AlertBanner';
import LoginView from './components/auth/LoginView';
import OnboardingModal from './components/auth/OnboardingModal';

import OverviewTab from './features/overview/OverviewTab';
import CandidatesTab from './features/candidates/CandidatesTab';
import TransactionsTab from './features/transactions/TransactionsTab';
import InventoryTab from './features/inventory/InventoryTab';
import ExtractorTab from './features/extractor/ExtractorTab';

export default function App() {
  const [session, setSession] = useState(null);
  const [loginError, setLoginError] = useState('');
  const [googleClientId, setGoogleClientId] = useState('');
  const [showProfileModal, setShowProfileModal] = useState(false);

  const [activeTab, setActiveTab] = useState('overview');
  const [summary, setSummary] = useState(null);
  const [inventory, setInventory] = useState([]);
  const [parties, setParties] = useState([]);
  const [transactions, setTransactions] = useState([]);
  const [candidates, setCandidates] = useState([]);

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [actionMessage, setActionMessage] = useState(null);

  const MERCHANT_ID = session?.merchant_id || '';

  // Handle automatic 401 unauthorization
  useEffect(() => {
    setOnUnauthorized(() => setSession(null));
  }, []);

  // Initialize session & auth config on mount
  useEffect(() => {
    fetchSession()
      .then((data) => {
        if (data) setSession(data);
      })
      .catch(console.error)
      .finally(() => setLoading(false));

    fetchAuthConfig()
      .then((cfg) => {
        if (cfg?.google_client_id) setGoogleClientId(cfg.google_client_id);
      })
      .catch(console.error);
  }, []);

  // Google OAuth Login handler
  const handleGoogleLogin = async (credential) => {
    try {
      setLoginError('');
      const userSession = await loginWithGoogle(credential);
      setSession(userSession);
    } catch (err) {
      setLoginError(err.message || 'Google sign-in failed');
    }
  };

  // Google Sign-In button injection
  useEffect(() => {
    if (session || !googleClientId) return;
    const interval = setInterval(() => {
      if (window.google?.accounts?.id) {
        clearInterval(interval);
        window.google.accounts.id.initialize({
          client_id: googleClientId,
          callback: (resp) => handleGoogleLogin(resp.credential),
        });
        const container = document.getElementById('google-signin-btn');
        if (container) {
          container.innerHTML = '';
          window.google.accounts.id.renderButton(container, {
            theme: 'filled_black',
            size: 'large',
            shape: 'pill',
            width: 280,
            text: 'continue_with',
          });
        }
      }
    }, 200);
    return () => clearInterval(interval);
  }, [session, googleClientId]);

  // Demo Login handler
  const handleDemoLogin = async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      setLoginError('');
      const userSession = await loginWithDemo(
        form.get('username') || 'demo',
        form.get('password') || ''
      );
      setSession(userSession);
    } catch (err) {
      setLoginError(err.message || 'Sign-in failed. Please verify credentials.');
    }
  };

  // Onboarding completion handler
  const handleOnboardComplete = async (profileData) => {
    const updated = await onboardUser(profileData);
    setSession(updated);
    setShowProfileModal(false);
    setActionMessage({
      type: 'success',
      text: `Merchant setup complete! Welcome ${updated.business_name} to Clandexis.`,
    });
    // Trigger initial fetch immediately
    if (updated.merchant_id) {
      fetchDashboardData(updated.merchant_id);
    }
  };

  // Logout handler
  const handleLogout = async () => {
    await logoutUser();
    setSession(null);
    setShowProfileModal(false);
    setSummary(null);
    setInventory([]);
    setParties([]);
    setTransactions([]);
    setCandidates([]);
  };

  // Fetch all dashboard data for the active merchant
  const fetchDashboardData = useCallback(async (merchantId) => {
    const targetId = merchantId || MERCHANT_ID;
    if (!targetId) return;

    try {
      setRefreshing(true);
      const [sum, inv, part, txns, cands] = await Promise.all([
        fetchSummary(targetId),
        fetchInventory(targetId),
        fetchParties(targetId),
        fetchTransactions(targetId),
        fetchCandidates(targetId),
      ]);

      if (sum) setSummary(sum);
      if (inv) setInventory(inv);
      if (part) setParties(part);
      if (txns) setTransactions(txns);
      if (cands) setCandidates(cands);
    } catch (err) {
      console.error('Dashboard fetch error:', err);
    } finally {
      setRefreshing(false);
    }
  }, [MERCHANT_ID]);

  // Polling effect every 8 seconds
  useEffect(() => {
    if (!session || session.needs_onboarding || !MERCHANT_ID) return;
    fetchDashboardData(MERCHANT_ID);
    const interval = setInterval(() => fetchDashboardData(MERCHANT_ID), 8000);
    return () => clearInterval(interval);
  }, [session, MERCHANT_ID, fetchDashboardData]);

  // Candidate Actions
  const handleConfirmCandidate = async (candidateId) => {
    try {
      const data = await confirmCandidate(MERCHANT_ID, candidateId);
      setActionMessage({
        type: 'success',
        text: `Transaction committed! Txn ID: ${data.txn_id}`,
      });
      fetchDashboardData();
    } catch (e) {
      setActionMessage({
        type: 'error',
        text: `Commit failed: ${e.message}`,
      });
    }
  };

  const handleCancelCandidate = async (candidateId) => {
    try {
      await cancelCandidate(MERCHANT_ID, candidateId);
      setActionMessage({
        type: 'info',
        text: 'Candidate marked as CANCELLED.',
      });
      fetchDashboardData();
    } catch (e) {
      setActionMessage({ type: 'error', text: e.message });
    }
  };

  const handleClarifyCandidate = async (candidateId, answer, revision) => {
    try {
      const data = await clarifyCandidate(candidateId, answer, revision);
      setActionMessage({
        type: 'info',
        text: data.question || 'Reply saved. Review updated details before confirming.',
      });
      fetchDashboardData();
    } catch (e) {
      setActionMessage({ type: 'error', text: e.message });
    }
  };

  const handleReviewCandidate = async (candidateId, candidateFacts, note) => {
    try {
      const data = await reviewCandidate(candidateId, candidateFacts, note);
      setActionMessage({
        type: 'info',
        text: data.review_resolved
          ? 'Review saved. Check details and confirm.'
          : data.detail || 'Still requires review.',
      });
      fetchDashboardData();
    } catch (e) {
      setActionMessage({ type: 'error', text: e.message });
    }
  };

  // If not logged in, render Login view
  if (!session) {
    return (
      <LoginView
        googleClientId={googleClientId}
        onGoogleLogin={handleGoogleLogin}
        onDemoLogin={handleDemoLogin}
        loginError={loginError}
      />
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans">
      {/* Post-OAuth Onboarding Modal or Settings Modal */}
      {(session.needs_onboarding || showProfileModal) && (
        <OnboardingModal
          session={session}
          onComplete={handleOnboardComplete}
          onCancel={session.needs_onboarding ? handleLogout : () => setShowProfileModal(false)}
        />
      )}

      {/* Top Navigation */}
      <Header
        session={session}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        refreshing={refreshing}
        onRefresh={() => fetchDashboardData()}
        onLogout={handleLogout}
        onOpenProfile={() => setShowProfileModal(true)}
      />

      {/* Global Alert Notification Banner */}
      <AlertBanner
        actionMessage={actionMessage}
        onClose={() => setActionMessage(null)}
      />

      {/* Main Content View */}
      <main className="flex-1 p-6 max-w-7xl w-full mx-auto space-y-6">
        {activeTab === 'overview' && (
          <OverviewTab
            summary={summary}
            parties={parties}
            inventory={inventory}
            candidates={candidates}
            onConfirmCandidate={handleConfirmCandidate}
            onCancelCandidate={handleCancelCandidate}
            onClarifyCandidate={handleClarifyCandidate}
            onReviewCandidate={handleReviewCandidate}
          />
        )}

        {activeTab === 'candidates' && (
          <CandidatesTab
            candidates={candidates}
            onConfirmCandidate={handleConfirmCandidate}
          />
        )}

        {activeTab === 'transactions' && (
          <TransactionsTab transactions={transactions} />
        )}

        {activeTab === 'inventory' && (
          <InventoryTab inventory={inventory} />
        )}

        {activeTab === 'demo' && (
          <ExtractorTab
            session={session}
            merchantId={MERCHANT_ID}
            onExtractionSuccess={() => fetchDashboardData()}
            onNavigateTab={(tab) => setActiveTab(tab)}
          />
        )}
      </main>

      {/* Global Footer */}
      <Footer />
    </div>
  );
}
