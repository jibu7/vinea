export interface CompanySummary {
  id: number;
  name: string;
  status: "active" | "suspended" | "cancelled";
  fiscal_country: string;
}

export interface MembershipSummary {
  id: number;
  company_id: number;
  company_name: string;
  is_owner: boolean;
}

export interface MeResponse {
  user_id: number;
  email: string;
  full_name: string;
  is_platform_admin: boolean;
  is_email_verified: boolean;
  company: CompanySummary | null;
  memberships: MembershipSummary[];
  permissions: string[];
  impersonated_by: number | null;
}

export interface SessionResponse {
  user_id: number;
  email: string;
  full_name: string;
  company_id: number | null;
  membership_id: number | null;
  access_expires_at: string;
}

export interface LoginPayload {
  email: string;
  password: string;
  company_id?: number;
}

export interface SignupPayload {
  company_name: string;
  full_name: string;
  email: string;
  password: string;
  tin?: string;
  vat_registered: boolean;
}
