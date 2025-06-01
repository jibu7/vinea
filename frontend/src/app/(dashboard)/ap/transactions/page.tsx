'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export default function APTransactionsPage() {
  const router = useRouter();

  useEffect(() => {
    // Redirect to the main AP page which already handles transaction listing
    router.push('/ap');
  }, [router]);

  return null;
} 