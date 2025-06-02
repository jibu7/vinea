'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Plus, FileText } from 'lucide-react';

export default function OEDocumentTypesPage() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Order Entry Document Types</h1>
          <p className="text-muted-foreground">
            Configure document types for sales orders, purchase orders, and related documents
          </p>
        </div>
        <Button>
          <Plus className="mr-2 h-4 w-4" />
          New Document Type
        </Button>
      </div>

      <div className="grid gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5" />
              Document Type Configuration
            </CardTitle>
            <CardDescription>
              Define document types for Order Entry module
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-center py-12">
              <FileText className="mx-auto h-12 w-12 text-muted-foreground" />
              <h3 className="mt-4 text-lg font-medium">No document types configured</h3>
              <p className="mt-2 text-muted-foreground">
                Get started by creating your first OE document type.
              </p>
              <Button className="mt-4">
                <Plus className="mr-2 h-4 w-4" />
                Create Document Type
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
