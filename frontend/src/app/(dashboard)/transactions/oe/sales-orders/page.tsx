'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Plus, ClipboardList } from 'lucide-react';

export default function SalesOrdersPage() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Sales Order Creation/Management</h1>
          <p className="text-muted-foreground">
            Create and manage customer sales orders
          </p>
        </div>
        <Button>
          <Plus className="mr-2 h-4 w-4" />
          New Sales Order
        </Button>
      </div>

      <div className="grid gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ClipboardList className="h-5 w-5" />
              Sales Orders
            </CardTitle>
            <CardDescription>
              Manage customer sales orders and fulfillment
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-center py-12">
              <ClipboardList className="mx-auto h-12 w-12 text-muted-foreground" />
              <h3 className="mt-4 text-lg font-medium">No sales orders found</h3>
              <p className="mt-2 text-muted-foreground">
                Get started by creating your first sales order.
              </p>
              <Button className="mt-4">
                <Plus className="mr-2 h-4 w-4" />
                Create Sales Order
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
