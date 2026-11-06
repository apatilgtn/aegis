"use server";

import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

export type AuthState = {
  error?: string;
  message?: string;
};

const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function authenticate(_state: AuthState, formData: FormData): Promise<AuthState> {
  const intent = formData.get("intent") === "signup" ? "signup" : "signin";
  const email = String(formData.get("email") ?? "").trim().toLowerCase();
  const password = String(formData.get("password") ?? "");
  const displayName = String(formData.get("displayName") ?? "").trim();

  if (!emailPattern.test(email) || password.length < 1) {
    return { error: "Enter a valid email address and password." };
  }

  if (intent === "signup" && (displayName.length < 2 || password.length < 12)) {
    return { error: "For a new account, enter your name and use at least 12 password characters." };
  }

  const supabase = await createClient();

  if (intent === "signup") {
    const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";
    const { data, error } = await supabase.auth.signUp({
      email,
      password,
      options: {
        data: { display_name: displayName },
        emailRedirectTo: `${siteUrl.replace(/\/$/, "")}/auth/callback`,
      },
    });

    if (error) {
      console.error("[Aegis Auth] Sign-up error:", error.code, error.message);
      if (error.code === "over_email_send_rate_limit" || error.message?.toLowerCase().includes("rate limit")) {
        return { error: "Supabase email rate limit reached. Disable 'Confirm email' in Supabase Auth → Providers → Email, or wait before retrying." };
      }
      if (error.code === "email_address_invalid") {
        return { error: `Supabase rejected this email address (${error.message}). Use a real email domain (e.g. @gmail.com).` };
      }
      if (error.code === "user_already_exists") {
        return { error: "An account with this email already exists. Switch to 'Sign in'." };
      }
      return { error: `Could not create account: ${error.message}` };
    }
    if (!data.session) return { message: "Check your email to verify the account (or confirm the user in Supabase Dashboard → Authentication → Users), then sign in." };
  } else {
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) {
      console.error("[Aegis Auth] Sign-in error:", error.code, error.message);
      if (error.code === "email_not_confirmed" || error.message?.toLowerCase().includes("email not confirmed")) {
        return { error: "Your email is not confirmed yet. Click the verification link in your email, or confirm the user in Supabase Dashboard → Authentication → Users." };
      }
      return { error: `Sign-in failed: ${error.message}` };
    }
  }

  redirect("/");
}
