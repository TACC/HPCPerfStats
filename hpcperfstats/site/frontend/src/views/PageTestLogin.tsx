"use client";

import { TextLink } from "@/components/TextLink";
import { useState, type FormEvent } from "react";
import BannerErrorMessage from "../components/BannerErrorMessage";
import LoadingMessage from "../components/LoadingMessage";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useTestLoginUser } from "@/hooks/use-test-login-user";
import { useDocumentTitle } from "../utils/useDocumentTitle";

export default function PageTestLogin() {
  const { data, error, loading, save, saving, saveError } = useTestLoginUser();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [actionError, setActionError] = useState("");

  useDocumentTitle("Create test user");

  const displayError = actionError || saveError || "";
  const unavailable = Boolean(error && !data);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    setActionError("");
    try {
      await save(username, password);
      setPassword("");
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Unable to save test user.");
    }
  }

  if (loading) {
    return <LoadingMessage message="Loading test user…" />;
  }

  if (unavailable) {
    return (
      <div className="mx-auto max-w-[640px] px-4">
        <p className="mb-3">
          <TextLink href="/machine/">Back to HPCPerfStats</TextLink>
        </p>
        <BannerErrorMessage message="Test login is not available on this site." />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[640px] px-4">
      <p className="mb-3">
        <TextLink href="/machine/">Back to HPCPerfStats</TextLink>
      </p>
      <main id="test-login-create-main">
        <Card className="shadow-sm">
          <CardHeader>
            <CardTitle className="text-xl">Create test user</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Development only. This replaces the single test-login account.
            </p>
            {data?.configured ? (
              <p>
                Configured username: <strong>{data.username}</strong>
              </p>
            ) : (
              <p>No test user is configured yet.</p>
            )}
            {data?.login_url ? (
              <p>
                Hidden login page:{" "}
                <a className="underline" href={data.login_url}>
                  {data.login_url}
                </a>
              </p>
            ) : null}
            {displayError ? (
              <Alert role="status">
                <AlertDescription>{displayError}</AlertDescription>
              </Alert>
            ) : null}
            <form className="space-y-3" onSubmit={(event) => void handleSubmit(event)}>
              <div className="space-y-1">
                <Label htmlFor="test-login-username">Username</Label>
                <Input
                  id="test-login-username"
                  name="username"
                  autoComplete="off"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  required
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="test-login-password">Password</Label>
                <Input
                  id="test-login-password"
                  name="password"
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                />
              </div>
              <Button type="submit" disabled={saving}>
                {saving ? "Saving…" : "Save test user"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
