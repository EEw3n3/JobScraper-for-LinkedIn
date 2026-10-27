"""Words that application forms use, in one place for every form handler.

English, Latvian and Russian first (the languages of cv.lv and Latvian career sites),
plus the most common Estonian, Lithuanian and German ones for Baltic and DACH forms.
Every entry is matched as a lowercase substring of a button's text or aria-label.
"""

# Opens the application form from a job page.
APPLY = (
    "apply for this job", "apply for this position", "apply for job", "apply now", "apply", "i'm interested",
    "i am interested", "pieteikties", "pieteikšanās", "piesakies", "sūtīt cv", "nosūtīt cv", "esmu ieinteresēt",
    "откликнуться", "подать заявку", "отправить резюме", "подать резюме", "мне интересно", "я заинтересован",
    "kandideeri", "kandideerin", "pateikti paraišką", "teikti paraišką", "aplikuoti", "jetzt bewerben", "bewerben",
)
# The last button of a form.
SUBMIT = (
    "submit application", "submit your application", "submit", "send application", "send", "apply now",
    "complete application", "finish application", "apply",
    "iesniegt pieteikumu", "iesniegt", "nosūtīt pieteikumu", "nosūtīt", "sūtīt", "pieteikties", "apstiprināt pieteikumu",
    "отправить заявку", "отправить отклик", "отправить", "откликнуться", "подать заявку",
    "saada", "esita", "kandideeri", "siųsti", "pateikti", "bewerbung absenden", "absenden",
    # ReachMee names its final button "Register" (the application, not an account).
    "register",
)
# Moves a multi-step form on.
NEXT = (
    "continue to next step", "next step", "next", "continue", "proceed",
    "nākamais", "nākošais", "tālāk", "turpināt",
    "далее", "продолжить", "следующий шаг", "дальше",
    "edasi", "jätka", "toliau", "tęsti", "weiter",
)
# Buttons that look like actions but must never be pressed by the applier: drafts, sign-ins, other ways to
# apply and leaving the form.
NEVER_CLICK = (
    "save for later", "save draft", "save and continue later", "saglabāt", "melnraksts", "сохранить",
    "cancel", "atcelt", "отмена", "отменить", "back", "atpakaļ", "назад",
    "sign in", "sign up", "log in", "login", "ienākt", "pieslēgties", "autorizēties", "войти",
    "create account", "create an account", "register an account", "reģistrēties", "izveidot kontu",
    "зарегистрироваться", "создать аккаунт",
    "with linkedin", "with indeed", "with google", "with facebook", "with xing", "with seek", "ar linkedin",
    "ar indeed", "ar google", "ar facebook", "через linkedin", "через indeed", "через google",
    "dropbox", "google drive", "onedrive", "profila foto", "profile photo", "refer a friend", "ieteikt draugu",
    "share", "dalīties", "поделиться",
)
# Text on the page after a successful application.
DONE = (
    "thank you for applying", "thanks for applying", "thank you for your application", "application has been received",
    "application received", "application was submitted", "application has been submitted", "application submitted",
    "application has been sent", "application was sent", "we have received your application",
    "we received your application", "successfully submitted", "application is complete",
    "paldies par pieteikumu", "paldies par jūsu pieteikumu", "paldies par tavu pieteikumu", "pieteikums ir saņemts",
    "pieteikums ir nosūtīts", "pieteikums nosūtīts", "pieteikums veiksmīgi", "veiksmīgi nosūtīts",
    "спасибо за отклик", "спасибо за заявку", "спасибо за ваш отклик", "заявка отправлена", "заявка получена",
    "отклик отправлен", "резюме отправлено", "ваша заявка принята",
    "aitäh kandideerimast", "täname kandideerimast", "ačiū, kad", "danke für ihre bewerbung",
)
# The site wants an applicant account first.
LOGIN = (
    "sign in to apply", "log in to apply", "login to apply", "create an account", "create account to apply",
    "register to apply", "ienāc, lai pieteiktos", "reģistrējies, lai pieteiktos", "lai pieteiktos, ienāciet",
    "войдите, чтобы откликнуться", "зарегистрируйтесь, чтобы",
)
# The job page says the vacancy is closed.
CLOSED = (
    "no longer available", "no longer accepting applications", "position has been filled", "job has expired",
    "job is closed", "job is no longer", "posting has expired", "position is closed",
    "vakance vairs nav", "vakance ir slēgta", "pieteikšanās ir noslēgusies", "pieteikšanās ir beigusies",
    "вакансия закрыта", "вакансия больше не", "больше не доступна",
)
# The site sends a one-time code by e-mail before the form (the applier does not read codes from the mailbox).
EMAIL_CODE = (
    "verify your email", "verify your e-mail", "one-time password", "one time password", "get otp", "send otp",
    "verification code", "verifikācijas kod", "apstiprinājuma kod", "vienreizējo paroli", "код подтверждения",
    "одноразовый код", "kinnituskood",
)
# Cookie banners: only the most privacy-preserving choice is ever clicked.
COOKIE_DECLINE = (
    "reject all", "decline all", "reject", "decline", "only necessary", "necessary only", "use necessary",
    "allow necessary", "essential only", "only essential", "deny",
    "noraidīt visas", "noraidīt", "atteikties", "tikai nepieciešam", "tikai obligāt",
    "отклонить все", "отклонить", "только необходимые",
    "keeldu", "ainult vajalikud", "atmesti visus", "atmesti", "tik būtini", "alle ablehnen", "ablehnen", "nur notwendige",
)
# Label marks of required and optional fields, besides "*".
REQUIRED = ("required", "mandatory", "obligāt", "jāaizpilda", "обязательн", "kohustuslik", "privalom", "pflicht")
OPTIONAL = ("optional", "neobligāt", "pēc izvēles", "необязательн", "по желанию", "valikuline", "neprivalom", "freiwillig")
