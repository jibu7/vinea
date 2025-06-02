'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { ClipboardList, Download } from 'lucide-react';

export default function InventoryListingPage() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Inventory Item Listing</h1>
          <p className="text-muted-foreground">
            View complete list of inventory items with details
          </p>
        </div>
        <Button>
          <Download className="mr-2 h-4 w-4" />
          Export Report
        </Button>
      </div>

      <div className="grid gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ClipboardList className="h-5 w-5" />
              Inventory Item Listing
            </CardTitle>
            <CardDescription>
              Complete listing of all inventory items with pricing and stock information
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-center py-12">
              <ClipboardList className="mx-auto h-12 w-12 text-muted-foreground" />
              <h3 className="mt-4 text-lg font-medium">No inventory items found</h3>
              <p className="mt-2 text-muted-foreground">
                Generate your inventory listing to view all item details.
              </p>
              <Button className="mt-4">
                <ClipboardList className="mr-2 h-4 w-4" />
                Generate Report
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
