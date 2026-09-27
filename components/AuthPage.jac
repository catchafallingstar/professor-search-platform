import from "@jac/runtime" { Link, useNavigate, useLocation, jacLogin, jacSignup }
import from "@hugeicons/react" { HugeiconsIcon }
import from "@hugeicons/core-free-icons" { Search01Icon, SparklesIcon, Clock01Icon, Analytics01Icon }
import from .ui.button { Button }
import from .ui.input { Input }
import from .ui.field { Field, FieldLabel, FieldGroup }
import from .AuthContext { refreshSession }

def:pub AuthBenefit(icon: any, title: str, text: str) -> JsxElement {
    return <div className="flex items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary-foreground/15">
            <HugeiconsIcon icon={icon} strokeWidth={2} className="size-4" />
        </div>
        <div>
            <p className="font-medium">{title}</p>
            <p className="text-sm text-primary-foreground/75">{text}</p>
        </div>
    </div>;
}

def:pub AuthPage() -> JsxElement {
    has mode: str = "signin",
        email: str = "",
        password: str = "",
        busy: bool = False,
        error: str = "";

    nav = useNavigate();
    loc = useLocation();

    async def submit(e: any) -> None {
        e.preventDefault();
        if not email.strip() or not password {
            error = "Enter your email and password.";
            return;
        }
        if mode == "signup" and len(password) < 8 {
            error = "Use at least 8 characters for your password.";
            return;
        }
        busy = True;
        error = "";
        signup_result: dict | None = None;
        ok: bool = False;
        who: any = None;
        addr = email.strip().lower();
        try {
            if mode == "signup" {
                signup_result = await jacSignup(addr, password);
                if not signup_result["success"] {
                    error = "Could not create the account. The email may already be registered.";
                    busy = False;
                    return;
                }
            }
            ok = await jacLogin(addr, password);
            if not ok {
                error = "Email or password is incorrect.";
                busy = False;
                return;
            }
            who = await refreshSession(addr);
        } except Exception as ex {
            error = "Sign-in failed: " + str(ex);
            busy = False;
            return;
        }
        busy = False;
        params: any = new(URLSearchParams, loc.search);  # jac:ignore[E1053]
        nxt: str = params.get("next") or "";
        if nxt and nxt.startswith("/") {
            nav(nxt, {"replace": True});
        } elif who and who.is_staff {
            nav("/staff", {"replace": True});
        } else {
            nav("/account", {"replace": True});
        }
    }

    is_signup = mode == "signup";

    return <div className="grid min-h-[calc(100svh-3.5rem)] lg:grid-cols-2">
        <div className="relative hidden overflow-hidden bg-primary text-primary-foreground lg:flex lg:flex-col lg:justify-between pt-10 pb-10 pl-10 pr-10">
            <div className="pointer-events-none absolute -right-24 -top-24 size-96 rounded-full bg-primary-foreground/10 blur-3xl" />
            <div className="pointer-events-none absolute -bottom-32 -left-16 size-96 rounded-full bg-primary-foreground/10 blur-3xl" />
            <div className="relative flex items-center gap-2.5">
                <div className="flex size-8 items-center justify-center rounded-lg bg-primary-foreground/15 ring-1 ring-primary-foreground/20">
                    <HugeiconsIcon icon={Search01Icon} strokeWidth={2} className="size-4" />
                </div>
                <span className="text-base font-semibold tracking-tight">{"Professor Atlas"}</span>
            </div>
            <div className="relative flex max-w-md flex-col gap-8">
                <h1 className="text-balance text-4xl font-semibold leading-tight tracking-tight">{"Find the right professor, backed by their real research."}</h1>
                <div className="flex flex-col gap-4">
                    <AuthBenefit icon={Analytics01Icon} title="Verified faculty" text="Official university directories, matched to OpenAlex papers and grants." />
                    <AuthBenefit icon={SparklesIcon} title="Hiring statements" text="Only quotes that actually appear on the professor's own pages." />
                    <AuthBenefit icon={Clock01Icon} title="Your history" text="Searches and professors you viewed are saved to your account." />
                </div>
            </div>
            <p className="relative text-sm text-primary-foreground/80">{"Department comes from the university. Research subfields come from recent papers."}</p>
        </div>
        <div className="flex flex-col bg-background pt-8 pb-8 pl-6 pr-6 sm:pl-10 sm:pr-10">
            <div className="flex flex-1 items-center justify-center">
                <form onSubmit={submit} className="flex w-full max-w-sm flex-col gap-6 animate-in fade-in slide-in-from-bottom-2 duration-500">
                    <div className="flex flex-col gap-1.5">
                        <h2 className="text-2xl font-semibold tracking-tight">{"Create your account" if is_signup else "Welcome back"}</h2>
                        <p className="text-sm text-muted-foreground">{"Save your searches and the professors you look at." if is_signup else "Sign in to see your search and viewing history."}</p>
                    </div>
                    <div className="grid grid-cols-2 gap-1 rounded-lg bg-muted pt-1 pb-1 pl-1 pr-1">
                        <button type="button" onClick={lambda { mode = "signin"; error = ""; }}
                            className={"rounded-md pt-1.5 pb-1.5 text-sm font-medium transition-colors " + ("bg-background shadow-sm" if not is_signup else "text-muted-foreground hover:text-foreground")}>{"Sign in"}</button>
                        <button type="button" onClick={lambda { mode = "signup"; error = ""; }}
                            className={"rounded-md pt-1.5 pb-1.5 text-sm font-medium transition-colors " + ("bg-background shadow-sm" if is_signup else "text-muted-foreground hover:text-foreground")}>{"Create account"}</button>
                    </div>
                    <FieldGroup>
                        <Field>
                            <FieldLabel htmlFor="auth-email">{"Email"}</FieldLabel>
                            <Input id="auth-email" type="email" autoComplete="email" placeholder="you@university.edu" value={email}
                                onChange={lambda (e: ChangeEvent) { email = e.target.value; }} className="h-10" />
                        </Field>
                        <Field>
                            <FieldLabel htmlFor="auth-password">{"Password"}</FieldLabel>
                            <Input id="auth-password" type="password" autoComplete={"new-password" if is_signup else "current-password"}
                                placeholder={"At least 8 characters" if is_signup else "Your password"} value={password}
                                onChange={lambda (e: ChangeEvent) { password = e.target.value; }} className="h-10" />
                        </Field>
                    </FieldGroup>
                    {error and <p className="text-sm text-destructive">{error}</p>}
                    <Button type="submit" className="h-10 w-full" disabled={busy}>
                        {("Creating account" if is_signup else "Signing in") if busy else ("Create account" if is_signup else "Sign in")}
                    </Button>
                    <p className="text-center text-sm text-muted-foreground">
                        {"You can search without an account. "}
                        <Link to="/" className="font-medium text-foreground underline-offset-4 hover:underline">{"Back to search"}</Link>
                    </p>
                </form>
            </div>
        </div>
    </div>;
}
