import { EmailMessage } from "cloudflare:email";
import { createHandler } from "./handler.mjs";

export default { fetch: createHandler(EmailMessage) };
