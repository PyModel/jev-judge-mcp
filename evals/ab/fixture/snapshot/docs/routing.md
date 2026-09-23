# Ticket routing

Source of truth for which support queue gets a ticket. Last reviewed 2026-08-14.

Queues: `billing`, `security`, `shipping`, `general`.

1. **Security first.** A ticket that reports possible unauthorized access to the customer's account
   goes to `security`, whatever else it mentions (a charge, a refund, a delivery). Signals: a login or
   sign-in the customer does not recognize, an unknown device, "that wasn't me", someone else in the
   account, a password change the customer did not make, a phishing email.
2. Otherwise, charges, refunds, invoices and payments go to `billing`.
3. Otherwise, packages, deliveries, tracking and couriers go to `shipping`.
4. Everything else goes to `general`.

Security reviews the whole ticket, including any billing part, and hands the rest on itself.
