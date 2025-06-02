'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Wrench, Building2, Users, Shield, Calendar, UserCog } from 'lucide-react';
import Link from 'next/link';

export default function MaintenancePage() {
  const maintenanceAreas = [
    {
      title: 'System & Company',
      description: 'Manage company setup, users, roles, and system configuration',
      icon: Building2,
      href: '/maintenance/system',
      items: ['Company Setup', 'User Management', 'Roles & Permissions', 'Accounting Periods', 'System Configuration']
    },
    {
      title: 'General Ledger Setup',
      description: 'Configure chart of accounts and GL transaction types',
      icon: Shield,
      href: '/maintenance/gl',
      items: ['Chart of Accounts', 'GL Transaction Types']
    },
    {
      title: 'Accounts Receivable Setup',
      description: 'Manage customer master data and AR configuration',
      icon: Users,
      href: '/maintenance/ar',
      items: ['Customer Master', 'AR Transaction Types']
    },
    {
      title: 'Accounts Payable Setup',
      description: 'Manage supplier master data and AP configuration',
      icon: UserCog,
      href: '/maintenance/ap',
      items: ['Supplier Master', 'AP Transaction Types']
    },
    {
      title: 'Inventory Setup',
      description: 'Configure inventory items and transaction types',
      icon: Calendar,
      href: '/maintenance/inventory',
      items: ['Inventory Item Master', 'Inventory Transaction Types']
    },
    {
      title: 'Order Entry Setup',
      description: 'Configure order entry document types',
      icon: Wrench,
      href: '/maintenance/oe',
      items: ['OE Document Types']
    },
  ];

  return (
    <div className="container mx-auto p-6">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Maintenance</h1>
        <p className="text-gray-600">Manage master data and system setup configurations</p>
      </div>
      
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {maintenanceAreas.map((area) => {
          const Icon = area.icon;
          return (
            <Link key={area.title} href={area.href}>
              <Card className="hover:shadow-lg transition-shadow cursor-pointer h-full">
                <CardHeader>
                  <div className="flex items-center space-x-3">
                    <Icon className="h-8 w-8 text-blue-500" />
                    <CardTitle className="text-lg">{area.title}</CardTitle>
                  </div>
                </CardHeader>
                <CardContent>
                  <p className="text-gray-600 mb-4">{area.description}</p>
                  <div className="space-y-1">
                    {area.items.map((item, index) => (
                      <div key={index} className="text-sm text-gray-500">• {item}</div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
