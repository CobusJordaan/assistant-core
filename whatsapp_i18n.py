"""Bilingual text lookup for WhatsApp replies (English + Afrikaans)."""

_STRINGS = {
    # --- Menus ---
    "main_menu_greeting": {
        "en": "Hi {name} \U0001f44b How can I help with your account today?",
        "af": "Hallo {name} \U0001f44b Waarmee kan ek jou vandag met jou rekening help?",
    },
    "main_menu_greeting_anon": {
        "en": "Hi \U0001f44b How can I help with your account today?",
        "af": "Hallo \U0001f44b Waarmee kan ek jou vandag met jou rekening help?",
    },
    "main_menu_1": {
        "en": "Account summary",
        "af": "Rekeningopsomming",
    },
    "main_menu_2": {
        "en": "Balance and unpaid invoices",
        "af": "Balans en oop fakture",
    },
    "main_menu_3": {
        "en": "Invoice or statement",
        "af": "Faktuur of staat",
    },
    "main_menu_4": {
        "en": "Support",
        "af": "Ondersteuning",
    },
    "main_menu_5": {
        "en": "Connection check",
        "af": "Konneksietoets",
    },

    # --- Document sub-menu ---
    "document_menu_preamble": {
        "en": "Sure \u2014 what would you like me to send?",
        "af": "Seker \u2014 wat wil jy h\u00ea ek moet stuur?",
    },
    "document_menu_1": {
        "en": "Latest invoice",
        "af": "Nuutste faktuur",
    },
    "document_menu_2": {
        "en": "Latest statement",
        "af": "Nuutste staat",
    },

    # --- Support sub-menu ---
    "support_menu_preamble": {
        "en": "What kind of issue are you experiencing?",
        "af": "Watter tipe probleem ondervind jy?",
    },
    "support_menu_1": {
        "en": "Connectivity / speed issue",
        "af": "Konneksie / spoed probleem",
    },
    "support_menu_2": {
        "en": "Billing or payment query",
        "af": "Rekening- of betalingsnavraag",
    },
    "support_menu_3": {
        "en": "General / other",
        "af": "Algemeen / ander",
    },

    # --- Invalid selection ---
    "invalid_selection": {
        "en": "I didn't recognise that option. Please reply with:",
        "af": "Ek het nie daardie opsie herken nie. Antwoord asseblief met:",
    },

    # --- Balance ---
    "balance_line": {
        "en": "{name} your account balance is *R{balance}*.",
        "af": "{name} jou rekeningbalans is *R{balance}*.",
    },
    "balance_name_prefix": {
        "en": "{first_name},",
        "af": "{first_name},",
    },
    "balance_anon_prefix": {
        "en": "Your",
        "af": "Jou",
    },
    "outstanding_line": {
        "en": "Outstanding invoices: *R{amount}* ({count} unpaid).",
        "af": "Uitstaande fakture: *R{amount}* ({count} onbetaal).",
    },
    "no_outstanding": {
        "en": "You have no outstanding invoices \u2705",
        "af": "Jy het geen uitstaande fakture nie \u2705",
    },

    # --- Invoices ---
    "no_unpaid_invoices": {
        "en": "Great news \u2014 you have no unpaid invoices! \u2705",
        "af": "Goeie nuus \u2014 jy het geen onbetaalde fakture nie! \u2705",
    },
    "unpaid_invoices_header": {
        "en": "You have *{count}* unpaid invoice{s} totalling *R{total}*:",
        "af": "Jy het *{count}* onbetaalde faktu{re} wat *R{total}* beloop:",
    },
    "invoices_more": {
        "en": "...and {n} more.",
        "af": "...en nog {n}.",
    },

    # --- Summary ---
    "summary_header": {
        "en": "*Account summary for {name}*",
        "af": "*Rekeningopsomming vir {name}*",
    },
    "summary_balance": {
        "en": "Balance: *R{balance}*",
        "af": "Balans: *R{balance}*",
    },
    "summary_outstanding": {
        "en": "Outstanding: *R{amount}*",
        "af": "Uitstaande: *R{amount}*",
    },
    "summary_package": {
        "en": "Package: {pkg_name} ({dl}/{ul} Mbps)",
        "af": "Pakket: {pkg_name} ({dl}/{ul} Mbps)",
    },

    # --- Invoice link ---
    "invoice_sent": {
        "en": "Your invoice *{num}* for *R{amount}* has been sent to you \u2705\nYou should receive it shortly.",
        "af": "Jou faktuur *{num}* vir *R{amount}* is aan jou gestuur \u2705\nJy behoort dit binnekort te ontvang.",
    },
    "invoice_send_failed": {
        "en": "I couldn't send your invoice right now. Please try again in a moment.",
        "af": "Ek kon nie nou jou faktuur stuur nie. Probeer asseblief weer oor 'n oomblik.",
    },
    "invoice_link_none": {
        "en": "You have no unpaid invoices on your account \u2705",
        "af": "Jy het geen onbetaalde fakture op jou rekening nie \u2705",
    },
    "invoice_not_found": {
        "en": "I couldn't find an invoice matching *{requested}* on your account. Please check the number and try again.",
        "af": "Ek kon nie 'n faktuur kry wat by *{requested}* pas op jou rekening nie. Kontroleer asseblief die nommer en probeer weer.",
    },
    "invoice_multiple_match": {
        "en": "I found more than one invoice matching *{requested}*: {candidates}.\nPlease send the full invoice number.",
        "af": "Ek het meer as een faktuur gevind wat by *{requested}* pas: {candidates}.\nStuur asseblief die volle faktuurnommer.",
    },

    # --- Statement link ---
    "statement_sent": {
        "en": "Your statement has been sent to you \u2705\nYou should receive it shortly.",
        "af": "Jou staat is aan jou gestuur \u2705\nJy behoort dit binnekort te ontvang.",
    },
    "statement_send_failed": {
        "en": "I couldn't send your statement right now. Please try again in a moment.",
        "af": "Ek kon nie nou jou staat stuur nie. Probeer asseblief weer oor 'n oomblik.",
    },

    # --- Latency ---
    "latency_ok_avg": {
        "en": "Ping to {host}: *{avg}ms* average. Connection looks OK from our side.",
        "af": "Ping na {host}: *{avg}ms* gemiddeld. Konneksie lyk reg van ons kant af.",
    },
    "latency_ok": {
        "en": "Ping to {host} was successful. Connection seems fine from our end.",
        "af": "Ping na {host} was suksesvol. Konneksie lyk reg van ons kant af.",
    },
    "latency_fail": {
        "en": "I couldn't reach {host} from our side either. This might indicate a wider issue. Our support team will investigate.",
        "af": "Ek kon nie {host} van ons kant af bereik nie. Dit kan 'n groter probleem aandui. Ons ondersteuningspan sal ondersoek.",
    },

    # --- Support ---
    "support_ack": {
        "en": "I'm sorry to hear you're having trouble. I've noted your message and our support team will follow up shortly.",
        "af": "Ek is jammer om te hoor jy het probleme. Ek het jou boodskap genoteer en ons ondersteuningspan sal binnekort opvolg.",
    },
    "support_category_selected": {
        "en": "*{label}* \u2014 got it.\n\nPlease describe your issue briefly and I'll create a support ticket for you.",
        "af": "*{label}* \u2014 het dit.\n\nBeskryf asseblief jou probleem kortliks en ek sal 'n ondersteuningskaartjie vir jou skep.",
    },
    "support_needs_account": {
        "en": "To create a support ticket I need to link your account first.\n\nPlease share your account number or the name on your account.",
        "af": "Om 'n ondersteuningskaartjie te skep moet ek eers jou rekening koppel.\n\nDeel asseblief jou rekeningnommer of die naam op jou rekening.",
    },
    "support_ticket_created": {
        "en": "Your support ticket *#{ticket}* has been created.\nCategory: {category}\n\nOur team will follow up with you shortly.",
        "af": "Jou ondersteuningskaartjie *#{ticket}* is geskep.\nKategorie: {category}\n\nOns span sal binnekort opvolg.",
    },
    "ai_ticket_created": {
        "en": "Got it — I've logged support ticket *#{ticket}* for you:\n_“{subject}”_\n\nOur team will follow up on this WhatsApp number shortly.",
        "af": "Reg so — ek het ondersteuningskaartjie *#{ticket}* vir jou aangeteken:\n_“{subject}”_\n\nOns span sal binnekort op hierdie WhatsApp-nommer opvolg.",
    },
    "support_ticket_failed": {
        "en": "I'm sorry, I couldn't create the ticket right now. Please try again in a moment, or contact us directly for support.",
        "af": "Ek is jammer, ek kon nie nou die kaartjie skep nie. Probeer asseblief weer oor 'n oomblik, of kontak ons direk vir ondersteuning.",
    },
    "support_ticket_error": {
        "en": "I'm sorry, something went wrong creating your ticket. Please try again in a moment.",
        "af": "Ek is jammer, iets het fout gegaan met die skep van jou kaartjie. Probeer asseblief weer oor 'n oomblik.",
    },

    # --- Needs client ---
    "needs_client": {
        "en": "I couldn't match your number to an account on our system. Could you please share your account number or the name on your account?",
        "af": "Ek kon nie jou nommer aan 'n rekening op ons stelsel koppel nie. Kan jy asseblief jou rekeningnommer of die naam op jou rekening deel?",
    },

    # --- Error ---
    "generic_error": {
        "en": "Sorry, something went wrong on my side. Please try again in a moment.",
        "af": "Jammer, iets het by my fout gegaan. Probeer asseblief weer oor 'n oomblik.",
    },

    # --- Unknown ---
    "unknown_fallback": {
        "en": "I'm not sure I understand. Please reply with a number from the menu, or type something like *balance*, *invoices*, or *account summary*.",
        "af": "Ek is nie seker ek verstaan nie. Antwoord asseblief met 'n nommer van die kieslys, of tik iets soos *balans*, *fakture*, of *rekeningopsomming*.",
    },

    # --- Follow-up after data ---
    "anything_else": {
        "en": "Is there anything else I can help with?",
        "af": "Is daar iets anders waarmee ek kan help?",
    },

    # --- Repeat ---
    "repeat_reply": {
        "en": "I just sent you that info. Is there anything else I can help with?",
        "af": "Ek het sopas daardie inligting gestuur. Is daar iets anders waarmee ek kan help?",
    },

    # --- Account lookup ---
    "lookup_error": {
        "en": "Sorry, I couldn't search for that right now. Please try again in a moment.",
        "af": "Jammer, ek kon nie daarvoor soek nie. Probeer asseblief weer oor 'n oomblik.",
    },
    "lookup_linked": {
        "en": "\u2705 Account *{number}* ({name}) linked.\n\n",
        "af": "\u2705 Rekening *{number}* ({name}) gekoppel.\n\n",
    },
    "lookup_verify_email": {
        "en": "I found account *{number}* ({name}).\n\nFor security, please confirm the email address on this account.\nHint: {masked}",
        "af": "Ek het rekening *{number}* ({name}) gevind.\n\nVir sekuriteit, bevestig asseblief die e-posadres op hierdie rekening.\nWenk: {masked}",
    },
    "lookup_multiple": {
        "en": "I found {count} accounts matching that. Could you share your account number? It usually starts with *DRA*.",
        "af": "Ek het {count} rekeninge gevind wat ooreenstem. Kan jy jou rekeningnommer deel? Dit begin gewoonlik met *DRA*.",
    },
    "lookup_none": {
        "en": "I couldn't find an account matching that. Please check and try again, or type *menu* to go back.",
        "af": "Ek kon nie 'n rekening kry wat ooreenstem nie. Kontroleer asseblief en probeer weer, of tik *menu* om terug te gaan.",
    },

    # --- Email verification ---
    "email_verified": {
        "en": "\u2705 Verified! Welcome, {name}.\n\n",
        "af": "\u2705 Geverifieer! Welkom, {name}.\n\n",
    },
    "email_failed": {
        "en": "That doesn't match the email on this account. For security, I can't provide account information.\n\nPlease send *Hi* to try again.",
        "af": "Dit stem nie ooreen met die e-pos op hierdie rekening nie. Vir sekuriteit kan ek nie rekeninginligting verskaf nie.\n\nStuur asseblief *Hi* om weer te probeer.",
    },

    # --- First-touch language choice (unlinked number, no preference yet) ---
    # Single key returns the same bilingual content in both lang slots since
    # we don't know the user's language yet at this point in the flow.
    "language_choice_prompt": {
        "en": (
            "Hi \U0001f44b Welcome / Welkom\n\n"
            "Please choose your language / Kies asseblief jou taal:\n\n"
            "*1.* English\n"
            "*2.* Afrikaans"
        ),
        "af": (
            "Hi \U0001f44b Welcome / Welkom\n\n"
            "Please choose your language / Kies asseblief jou taal:\n\n"
            "*1.* English\n"
            "*2.* Afrikaans"
        ),
    },
    "language_choice_invalid": {
        "en": (
            "Reply with *1* for English or *2* for Afrikaans.\n"
            "Antwoord met *1* vir Engels of *2* vir Afrikaans."
        ),
        "af": (
            "Reply with *1* for English or *2* for Afrikaans.\n"
            "Antwoord met *1* vir Engels of *2* vir Afrikaans."
        ),
    },

    # --- Strict identity-link flow (number not linked) ---
    "link_intro": {
        "en": "Hi \U0001f44b This WhatsApp number isn't linked to an account on our system yet.\n\nTo link it, please send your *account number* or *contract ID*.",
        "af": "Hallo \U0001f44b Hierdie WhatsApp-nommer is nog nie aan 'n rekening op ons stelsel gekoppel nie.\n\nOm dit te koppel, stuur asseblief jou *rekeningnommer* of *kontrak-ID*.",
    },
    "link_invalid_account_ref": {
        "en": "That doesn't look like an account number or contract ID. Please send the code shown on your invoice (e.g. *DRA0011* or *SDA000*).",
        "af": "Dit lyk nie soos 'n rekeningnommer of kontrak-ID nie. Stuur asseblief die kode op jou faktuur (bv. *DRA0011* of *SDA000*).",
    },
    "link_ask_contract": {
        "en": "Thanks. Now please send your *contract ID* for the same account.",
        "af": "Dankie. Stuur nou asseblief jou *kontrak-ID* vir dieselfde rekening.",
    },
    "link_ask_email": {
        "en": "Almost done. Please send the *email address* on file for this account.",
        "af": "Amper klaar. Stuur asseblief die *e-posadres* op rekord vir hierdie rekening.",
    },
    "link_success": {
        "en": "\u2705 Verified and linked! Welcome, {name}.\nThis WhatsApp number is now connected to account *{number}*.\n\n",
        "af": "\u2705 Geverifieer en gekoppel! Welkom, {name}.\nHierdie WhatsApp-nommer is nou aan rekening *{number}* gekoppel.\n\n",
    },
    "link_failed_number": {
        "en": "I couldn't find an account that matches that account number and contract ID together.",
        "af": "Ek kon nie 'n rekening kry wat ooreenstem met daardie rekeningnommer en kontrak-ID saam nie.",
    },
    "link_failed_email": {
        "en": "That email doesn't match the one on file for this account.",
        "af": "Daardie e-pos stem nie ooreen met die een op rekord vir hierdie rekening nie.",
    },
    "link_offer_ticket": {
        "en": "Would you like me to open a *support ticket* so our team can get back to you?\nReply *yes* to continue, or *no* to cancel.",
        "af": "Wil jy h\u00ea ek moet 'n *ondersteuningskaartjie* oopmaak sodat ons span kan terugkom na jou?\nAntwoord *ja* om voort te gaan, of *nee* om te kanselleer.",
    },
    "link_ticket_ask_description": {
        "en": "Got it. Please describe briefly how we can help, and I'll log a ticket for our team to follow up.",
        "af": "Reg so. Beskryf asseblief kortliks hoe ons kan help, en ek sal 'n kaartjie aanteken sodat ons span kan opvolg.",
    },
    "link_ticket_unlinked_created": {
        "en": "Your support ticket *#{ticket}* has been logged. Our team will reach out on this WhatsApp number shortly.",
        "af": "Jou ondersteuningskaartjie *#{ticket}* is aangeteken. Ons span sal binnekort op hierdie WhatsApp-nommer kontak maak.",
    },
    "link_ticket_cancelled": {
        "en": "No problem. If you change your mind, send *Hi* to start over.",
        "af": "Geen probleem nie. As jy van plan verander, stuur *Hi* om weer te begin.",
    },

    # --- Connection check (menu 5 / "is my line up") ---
    "connection_healthy_avg": {
        "en": "✅ Your connection is up.\nIP: *{ip}* — average ping {avg}ms from our side.",
        "af": "✅ Jou konneksie is op.\nIP: *{ip}* — gemiddelde ping {avg}ms van ons kant af.",
    },
    "connection_healthy": {
        "en": "✅ Your connection is up.\nIP: *{ip}* — reachable from our side.",
        "af": "✅ Jou konneksie is op.\nIP: *{ip}* — bereikbaar van ons kant af.",
    },
    "connection_offline": {
        "en": "⚠️ I can't see an active session for your line. It looks like your connection is down from our side.",
        "af": "⚠️ Ek kan nie 'n aktiewe sessie vir jou lyn sien nie. Dit lyk of jou konneksie van ons kant af af is.",
    },
    "connection_online_no_ping": {
        "en": "⚠️ Your session is up (IP *{ip}*) but I can't reach your equipment when I ping it. There may be an issue on the local side.",
        "af": "⚠️ Jou sessie is op (IP *{ip}*) maar ek kan nie jou toerusting bereik wanneer ek dit ping nie. Daar mag 'n probleem aan die plaaslike kant wees.",
    },
    "connection_online_no_ip": {
        "en": "⚠️ Your session is up but I don't have an IP to ping. Something looks off with the connection.",
        "af": "⚠️ Jou sessie is op maar ek het nie 'n IP om te ping nie. Iets lyk verkeerd met die konneksie.",
    },
    "connection_no_radius": {
        "en": "⚠️ I couldn't run an automatic check on your line — your account doesn't have a RADIUS username/MAC configured.",
        "af": "⚠️ Ek kon nie 'n outomatiese toets op jou lyn uitvoer nie — jou rekening het nie 'n RADIUS-gebruikersnaam/MAC opgestel nie.",
    },
    "connection_error": {
        "en": "⚠️ I couldn't run the connection check right now.",
        "af": "⚠️ Ek kon nie nou die konneksietoets uitvoer nie.",
    },
    "connection_offer_ticket": {
        "en": "Would you like me to open a *support ticket* so our team can take a look?\nReply *yes* to log a ticket, or *no* to skip.",
        "af": "Wil jy hê ek moet 'n *ondersteuningskaartjie* oopmaak sodat ons span kan kyk?\nAntwoord *ja* om 'n kaartjie aan te teken, of *nee* om oor te slaan.",
    },
    "connection_ticket_created": {
        "en": "✅ Support ticket *#{ticket}* logged. Our team will follow up shortly.",
        "af": "✅ Ondersteuningskaartjie *#{ticket}* aangeteken. Ons span sal binnekort opvolg.",
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    """Look up a translated string.

    Falls back to English if key or language is missing.
    """
    entry = _STRINGS.get(key, {})
    text = entry.get(lang) or entry.get("en", key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text
