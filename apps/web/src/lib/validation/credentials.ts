import { z } from "zod";

/** Client-side credential shape aligned with API field lengths (domain enforces the full policy). */
export const credentialsSchema = z.object({
  email: z
    .string()
    .trim()
    .min(3, "Enter your email address.")
    .max(320, "Email address is too long.")
    .email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password.").max(128, "Password is too long."),
});

export type CredentialsFormValues = z.infer<typeof credentialsSchema>;

export const registerCredentialsSchema = credentialsSchema.extend({
  password: z
    .string()
    .min(12, "Password must be at least 12 characters.")
    .max(128, "Password is too long."),
});
