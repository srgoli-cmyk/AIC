// What the privacy page's delete card shows in each state. Pure, so node tests can check it.

export function deleteState(kind, user) {
  switch (kind) {
    case "signed_in":
      return {
        text: `Signed in as ${typeof user?.email === "string" ? user.email : "your UCSD account"}. This deletes the account and all its data.`,
        button: true,
      };
    case "deleting":
      return { text: "Deleting…", button: true, busy: true };
    case "deleted":
      return { text: "Your account and all its data are deleted. You are signed out.", button: false };
    case "signed_out":
      return { text: "Sign in at /app/ first, then come back here to delete your account.", button: false };
    default:
      return { text: "Something went wrong. Try again in a moment.", button: true };
  }
}
