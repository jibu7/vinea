'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Plus, Coins } from 'lucide-react';

export default function ARPaymentsPage() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Customer Payment Processing & Allocation</h1>
          <p className="text-muted-foreground">
            Process customer payments and allocate to invoices
          </p>
        </div>
        <Button>
          <Plus className="mr-2 h-4 w-4" />
          New Payment
        </Button>
      </div>

      <div className="grid gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Coins className="h-5 w-5" />
              Customer Payments
            </CardTitle>
            <CardDescription>
              Process payments and allocate to outstanding invoices
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-center py-12">
              <Coins className="mx-auto h-12 w-12 text-muted-foreground" />
              <h3 className="mt-4 text-lg font-medium">No payments found</h3>
              <p className="mt-2 text-muted-foreground">
                Get started by processing your first customer payment.
              </p>
              <Button className="mt-4">
                <Plus className="mr-2 h-4 w-4" />
                Process Payment
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
