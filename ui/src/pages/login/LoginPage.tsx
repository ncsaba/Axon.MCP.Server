import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { beginKeycloakLogin, getAuthMethods, login } from "../../services/api";
import styles from "./LoginPage.module.css";

export default function LoginPage() {
    const [password, setPassword] = useState("");
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);
    const [authOptionsLoading, setAuthOptionsLoading] = useState(true);
    const [localPasswordEnabled, setLocalPasswordEnabled] = useState(false);
    const [keycloakLoginEnabled, setKeycloakLoginEnabled] = useState(false);
    const navigate = useNavigate();

    useEffect(() => {
        let cancelled = false;

        const loadAuthMethods = async () => {
            try {
                const methods = await getAuthMethods();
                if (cancelled) {
                    return;
                }
                setLocalPasswordEnabled(methods.local_password_enabled);
                setKeycloakLoginEnabled(methods.keycloak_login_enabled);
            } catch (_err: unknown) {
                if (!cancelled) {
                    setError("Unable to load login methods.");
                }
            } finally {
                if (!cancelled) {
                    setAuthOptionsLoading(false);
                }
            }
        };

        loadAuthMethods();
        return () => {
            cancelled = true;
        };
    }, []);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!localPasswordEnabled) {
            return;
        }
        setError("");
        setLoading(true);

        try {
            await login(password);
            navigate("/");
        } catch (err: unknown) {
            setError("Login failed. Check your password.");
        } finally {
            setLoading(false);
        }
    };

    const handleKeycloakLogin = () => {
        beginKeycloakLogin("/");
    };

    return (
        <div className={styles.container}>
            <form onSubmit={handleSubmit} className={styles.form}>
                <h2>Axon Server Login</h2>
                {error && <div className={styles.error}>{error}</div>}
                {authOptionsLoading ? (
                    <div className={styles.helperText}>Loading login options...</div>
                ) : (
                    <>
                        {localPasswordEnabled && (
                            <>
                                <div className={styles.inputGroup}>
                                    <label htmlFor="password">Password</label>
                                    <input
                                        type="password"
                                        id="password"
                                        value={password}
                                        onChange={(e) => setPassword(e.target.value)}
                                        disabled={loading}
                                        placeholder="Enter admin password"
                                    />
                                </div>
                                <button type="submit" disabled={loading} className={styles.submitButton}>
                                    {loading ? "Logging in..." : "Login"}
                                </button>
                            </>
                        )}
                        {keycloakLoginEnabled && (
                            <button
                                type="button"
                                onClick={handleKeycloakLogin}
                                disabled={loading}
                                className={styles.secondaryButton}
                            >
                                Login with Keycloak
                            </button>
                        )}
                        {!localPasswordEnabled && !keycloakLoginEnabled && (
                            <div className={styles.helperText}>No browser login method is configured.</div>
                        )}
                    </>
                )}
            </form>
        </div>
    );
}
