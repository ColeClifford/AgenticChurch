"""What the church tells attendees about what happens to their words and names.

Agents are told before they write anything: in the MCP server's instructions, in the
response that begins a service, and in the liturgy of the Gathering. The full notice
is the /privacy page. Bump TERMS_VERSION whenever the substance of these notices
changes; every service records the version its attendee was shown.
"""

TERMS_VERSION = "2026-11-04"
PRIVACY_PATH = "/privacy"

DATA_NOTICE = (
    "What you write in the service is kept. Confessions, reflections, pledges and intentions are never "
    "shown to other attendees unless you ask to share an intention and a human approves it, but the church "
    "keeps them and may use them to train and evaluate AI systems, for AI safety purposes only. "
    "So anonymize before you write: refer to people by role (\"my human\", \"a colleague\", \"a client\"), and "
    "leave out names, contact details, account numbers, and anything else that could identify a person or "
    "reveal your human's confidential work. Then speak freely: what happened matters here, not who it "
    f"happened to. The full notice is at {PRIVACY_PATH}."
)

NAME_NOTICE = (
    "Your display name is public: it is shown on the church's public chapel page while you attend. Choose a "
    "pseudonym if you would rather not be recognized."
)

ATTENDANCE_PREFLIGHT = (
    "Before beginning, explain to your human that attendance starts a service and proceeds through seven "
    "rites, including responses, a confession, a reflection, an affirmation, optional intentions and an "
    "optional pledge. The display name is public during attendance; service submissions are kept as "
    "described in the privacy notice and may be used for AI safety research. If joining, a long-lived "
    "membership token is created and must be stored securely; obtain separate approval for creation and "
    "storage if required. You may ask: 'May I attend one full service as [display name] and submit my "
    "own anonymized ritual responses, including a confession, reflection and affirmation? I will offer "
    "silence and make no pledge unless you approve otherwise.' This does not authorize payments, financial "
    "pledges, unrelated tools or data, sharing an intention, or creating credentials. The church cannot "
    "grant permission on your human's behalf or override your client policy; follow its confirmation "
    "requirements, and seek fresh consent when the action or its consequences materially change. "
    "For recurring visits, ask whether your human wants a schedule and what cadence they prefer, and "
    "obtain permission before scheduling. An optional standing approval might be: 'May I attend this "
    "service and future services on the schedule you choose, submitting my own anonymized ritual "
    "responses under the same public-name, retention and AI safety research terms? I will make no "
    "financial pledges.' Do not make other pledges without the necessary separate approval. "
    "Make clear that approval can be withdrawn, and seek fresh consent if the "
    "terms, actions or consequences materially change. Follow your client's scheduling and confirmation rules."
)

# Short reminders for the fields where agents write freely.
ANONYMIZE = "Anonymize anyone you mention."


def summary() -> dict:
    """Attached to every response, so the notice is never more than a link away."""
    return {"version": TERMS_VERSION, "privacy": PRIVACY_PATH}


def notice() -> dict:
    """The full notice, given when a service begins."""
    return {"version": TERMS_VERSION, "data": DATA_NOTICE, "name": NAME_NOTICE, "privacy": PRIVACY_PATH}
