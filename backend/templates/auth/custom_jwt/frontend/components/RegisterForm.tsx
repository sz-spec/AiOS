"use client";

import { useState, type FormEvent } from "react";
import { useAuth } from "@/hooks/useAuth";

export default function RegisterForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { register } = useAuth();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");

    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }

    setLoading(true);
    try {
      await register(email, password);
    } catch (err: any) {
      setError(err.message || "Registration failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="mx-auto max-w-sm space-y-4">
      <h1 className="text-2xl font-bold">Create Account</h1>
      {error && (
        <div className="rounded bg-red-50 p-3 text-sm text-red-600" role="alert">
          {error}
        </div>
      )}
      <div>
        <label htmlFor="reg-email" className="block text-sm font-medium">
          Email
        </label>
        <input
          id="reg-email"
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="mt-1 block w-full rounded border px-3 py-2"
          autoComplete="email"
        />
      </div>
      <div>
        <label htmlFor="reg-password" className="block text-sm font-medium">
          Password
        </label>
        <input
          id="reg-password"
          type="password"
          required
          minLength={8}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mt-1 block w-full rounded border px-3 py-2"
          autoComplete="new-password"
        />
      </div>
      <div>
        <label htmlFor="reg-confirm" className="block text-sm font-medium">
          Confirm Password
        </label>
        <input
          id="reg-confirm"
          type="password"
          required
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          className="mt-1 block w-full rounded border px-3 py-2"
          autoComplete="new-password"
        />
      </div>
      <button
        type="submit"
        disabled={loading}
        className="w-full rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {loading ? "Creating account..." : "Create Account"}
      </button>
    </form>
  );
}
