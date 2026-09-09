"""The AR/AP subledger — one symmetric partner-document module (Master Plan §5 P4).

`PartnerRole` is the discriminator throughout: there is no `ar_service` and no `ap_service`,
only role-parameterised code. Everything that reaches the general ledger does so through
`app.kernel.posting`.
"""
