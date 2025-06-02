'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Plus, FileText } from 'lucide-react';

export default function APCreditNotesPage() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Supplier Credit Note Processing</h1>
          <p className="text-muted-foreground">
            Create and manage supplier credit notes
          </p>
        </div>
        <Button>
          <Plus className="mr-2 h-4 w-4" />
          New Credit Note
        </Button>
      </div>

      <div className="grid gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5" />
              Supplier Credit Notes
            </CardTitle>
            <CardDescription>
              Process supplier credit notes and adjustments
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-center py-12">
              <FileText className="mx-auto h-12 w-12 text-muted-foreground" />
              <h3 className="mt-4 text-lg font-medium">No credit notes found</h3>
              <p className="mt-2 text-muted-foreground">
                Get started by creating your first supplier credit note.
              </p>
              <Button className="mt-4">
                <Plus className="mr-2 h-4 w-4" />
                Create Credit Note
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
