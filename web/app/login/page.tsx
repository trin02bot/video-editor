"use client";
import { useState } from "react";
import { supabase } from "@/lib/supabase";

export default function Login() {
  const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"in" | "up">("in"); const [msg, setMsg] = useState("");
  async function go(e: React.FormEvent) {
    e.preventDefault(); setMsg("");
    const res = mode === "in"
      ? await supabase.auth.signInWithPassword({ email, password })
      : await supabase.auth.signUp({ email, password, options: { emailRedirectTo: `${location.origin}/` } });
    if (res.error) return setMsg(res.error.message);
    if (mode === "up" && !res.data.session) return setMsg("Check your email to confirm your account.");
    location.href = "/";
  }
  return (
    <div style={{ maxWidth: 420, margin: "40px auto" }}>
      <h1>{mode === "in" ? "Sign in" : "Create your studio"}</h1>
      <form className="card" onSubmit={go}>
        <label>Email</label><input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <label>Password</label><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
        <div className="row" style={{ marginTop: 16, justifyContent: "space-between" }}>
          <button type="submit">{mode === "in" ? "Sign in" : "Create account"}</button>
          <a href="#" onClick={(e) => { e.preventDefault(); setMode(mode === "in" ? "up" : "in"); }}>
            {mode === "in" ? "New here? Create an account" : "Have an account? Sign in"}
          </a>
        </div>
        {msg && <p className="bad">{msg}</p>}
      </form>
    </div>
  );
}
