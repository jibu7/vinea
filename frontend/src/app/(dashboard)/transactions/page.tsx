'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ArrowUpDown, BookOpen, DollarSign, CreditCard, Package, ShoppingCart } from 'lucide-react';
import Link from 'next/link';

export default function TransactionsPage() {
  const transactionAreas = [
    {
      title: 'General Ledger',
      description: 'Process manual journal entries',
      icon: BookOpen,
      href: '/transactions/gl',
      items: ['Manual Journal Entry']
    },
    {
      title: 'Accounts Receivable',
      description: 'Process customer invoices, credit notes, and payments',
      icon: DollarSign,
      href: '/transactions/ar',
      items: ['Customer Invoice Processing', 'Customer Credit Note Processing', 'Customer Payment Processing & Allocation']
    },
    {
      title: 'Accounts Payable',
      description: 'Process supplier invoices, credit notes, and payments',
      icon: CreditCard,
      href: '/transactions/ap',
      items: ['Supplier Invoice Processing', 'Supplier Credit Note Processing', 'Supplier Payment Processing & Allocation']
    },
    {
      title: 'Inventory',
      description: 'Process inventory adjustments and transfers',
      icon: Package,
      href: '/transactions/inventory',
      items: ['Inventory Adjustments']
    },
    {
      title: 'Order Entry',
      description: 'Manage sales and purchase orders, GRV processing',
      icon: ShoppingCart,
      href: '/transactions/oe',
      items: ['Sales Order Creation/Management', 'Purchase Order Creation/Management', 'Goods Received Voucher (GRV) Processing']
    },
  ];

  return (
    <div className="container mx-auto p-6">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Transactions</h1>
        <p className="text-gray-600">Process day-to-day operational transactions and data entry</p>
      </div>
      
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {transactionAreas.map((area) => {
          const Icon = area.icon;
          return (
            <Link key={area.title} href={area.href}>
              <Card className="hover:shadow-lg transition-shadow cursor-pointer h-full">
                <CardHeader>
                  <div className="flex items-center space-x-3">
                    <Icon className="h-8 w-8 text-green-500" />
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
