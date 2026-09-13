"""
Auth Builder Service
====================
Generates authentication code for user-created apps.
Creates login/signup pages, protected routes, and Clerk Auth config.
"""

from typing import Dict, List


def generate_auth_code(providers: List[str]) -> Dict[str, str]:
    """
    Generate auth-related code for a project.

    Args:
        providers: List of auth providers ('email', 'google', 'github')

    Returns:
        Dict of file path -> file content
    """
    files = {}

    # Auth utility
    files["src/lib/auth.ts"] = _generate_auth_lib()

    # Login page
    files["src/pages/Login.tsx"] = _generate_login_page(providers)

    # Signup page
    files["src/pages/Signup.tsx"] = _generate_signup_page(providers)

    # Protected route wrapper
    files["src/components/ProtectedRoute.tsx"] = _generate_protected_route()

    return files


def _generate_auth_lib() -> str:
    return """// Auth handled by Clerk - see frontend/providers/
// Import { useUser, useAuth, useClerk } from "@clerk/nextjs" in your components.
// See https://clerk.com/docs for full documentation.

import { useSignIn, useSignUp, useUser } from "@clerk/nextjs";

export async function signIn(email: string, password: string) {
  // Delegate to Clerk's signIn flow
  try {
    const { signIn } = useSignIn();
    return await signIn!.create({ identifier: email, password });
  } catch (error: any) {
    return { error };
  }
}

export async function signUp(email: string, password: string) {
  try {
    const { signUp } = useSignUp();
    return await signUp!.create({ emailAddress: email, password });
  } catch (error: any) {
    return { error };
  }
}

export async function signInWithProvider(provider: string) {
  const { signIn } = useSignIn();
  return signIn!.authenticateWithRedirect({
    strategy: `oauth_${provider}` as any,
    redirectUrl: "/sso-callback",
    redirectUrlComplete: "/",
  });
}

export async function getUser() {
  const { user } = useUser();
  return user;
}
"""


def _generate_login_page(providers: List[str]) -> str:
    oauth_buttons = ""
    if "google" in providers:
        oauth_buttons += """
        <button onClick={() => signInWithProvider('google')} className="oauth-btn">
          Sign in with Google
        </button>"""
    if "github" in providers:
        oauth_buttons += """
        <button onClick={() => signInWithProvider('github')} className="oauth-btn">
          Sign in with GitHub
        </button>"""

    return f"""import React, {{ useState }} from 'react';
import {{ signIn, signInWithProvider }} from '../lib/auth';

export default function Login() {{
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {{
    e.preventDefault();
    setLoading(true);
    setError('');
    const {{ error }} = await signIn(email, password);
    if (error) setError(error.message);
    setLoading(false);
  }};

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ width: '100%', maxWidth: '400px', padding: '32px' }}>
        <h1>Sign In</h1>
        <form onSubmit={{handleSubmit}}>
          <input type="email" placeholder="Email" value={{email}} onChange={{e => setEmail(e.target.value)}} required />
          <input type="password" placeholder="Password" value={{password}} onChange={{e => setPassword(e.target.value)}} required />
          {{error && <p style={{ color: 'red' }}>{{error}}</p>}}
          <button type="submit" disabled={{loading}}>{{loading ? 'Signing in...' : 'Sign In'}}</button>
        </form>
        {oauth_buttons}
        <p>Don&apos;t have an account? <a href="/signup">Sign up</a></p>
      </div>
    </div>
  );
}}
"""


def _generate_signup_page(providers: List[str]) -> str:
    return """import React, { useState } from 'react';
import { signUp } from '../lib/auth';

export default function Signup() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    const { error } = await signUp(email, password);
    if (error) setError(error.message);
    else setSuccess(true);
    setLoading(false);
  };

  if (success) return <div><h1>Check your email</h1><p>We sent you a confirmation link.</p></div>;

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ width: '100%', maxWidth: '400px', padding: '32px' }}>
        <h1>Create Account</h1>
        <form onSubmit={handleSubmit}>
          <input type="email" placeholder="Email" value={email} onChange={e => setEmail(e.target.value)} required />
          <input type="password" placeholder="Password (6+ characters)" value={password} onChange={e => setPassword(e.target.value)} required minLength={6} />
          {error && <p style={{ color: 'red' }}>{error}</p>}
          <button type="submit" disabled={loading}>{loading ? 'Creating...' : 'Create Account'}</button>
        </form>
        <p>Already have an account? <a href="/login">Sign in</a></p>
      </div>
    </div>
  );
}
"""


def _generate_protected_route() -> str:
    return """import React, { useEffect, useState } from 'react';
import { getUser } from '../lib/auth';

interface Props {
  children: React.ReactNode;
}

export default function ProtectedRoute({ children }: Props) {
  const [user, setUser] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getUser().then(u => {
      setUser(u);
      setLoading(false);
      if (!u) window.location.href = '/login';
    });
  }, []);

  if (loading) return <div>Loading...</div>;
  if (!user) return null;
  return <>{children}</>;
}
"""
