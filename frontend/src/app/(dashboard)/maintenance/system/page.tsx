'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export default function MaintenanceSystemPage() {
  return (
    <div className="container mx-auto p-6">
      <h1 className="text-3xl font-bold mb-6">System & Company Setup</h1>
      
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <Card>
          <CardHeader>
            <CardTitle>Company Setup</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-gray-600">Configure company details and settings</p>
          </CardContent>
        </Card>
        
        <Card>
          <CardHeader>
            <CardTitle>User Management</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-gray-600">Manage system users and their access</p>
          </CardContent>
        </Card>
        
        <Card>
          <CardHeader>
            <CardTitle>Roles & Permissions</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-gray-600">Define user roles and permissions</p>
          </CardContent>
        </Card>
        
        <Card>
          <CardHeader>
            <CardTitle>Accounting Periods</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-gray-600">Manage financial periods</p>
          </CardContent>
        </Card>
        
        <Card>
          <CardHeader>
            <CardTitle>System Configuration</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-gray-600">Configure system defaults and settings</p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
