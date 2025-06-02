'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Plus, UserCog } from 'lucide-react';

export default function SystemConfigPage() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">System Configuration</h1>
          <p className="text-muted-foreground">
            Configure system-wide settings and defaults
          </p>
        </div>
      </div>

      <div className="grid gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <UserCog className="h-5 w-5" />
              System Settings
            </CardTitle>
            <CardDescription>
              Global system configuration and defaults
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-center py-12">
              <UserCog className="mx-auto h-12 w-12 text-muted-foreground" />
              <h3 className="mt-4 text-lg font-medium">System Configuration</h3>
              <p className="mt-2 text-muted-foreground">
                Configure system defaults and global settings.
              </p>
              <Button className="mt-4">
                <UserCog className="mr-2 h-4 w-4" />
                Configure System
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
