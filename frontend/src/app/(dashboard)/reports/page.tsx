'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { PieChart, BookOpen, DollarSign, CreditCard, Package, ShoppingCart } from 'lucide-react';
import Link from 'next/link';

export default function ReportsPage() {
  const reportAreas = [
    {
      title: 'Financial Statements & GL Reports',
      description: 'Generate trial balance and general ledger detail reports',
      icon: BookOpen,
      href: '/reports/gl',
      items: ['Trial Balance', 'GL Detail Report']
    },
    {
      title: 'Accounts Receivable Reports',
      description: 'Customer ageing, statements, and transaction reports',
      icon: DollarSign,
      href: '/reports/ar',
      items: ['Customer Ageing Report', 'Customer Statements', 'Customer Listing', 'AR Transaction Listing']
    },
    {
      title: 'Accounts Payable Reports',
      description: 'Supplier ageing, statements, and transaction reports',
      icon: CreditCard,
      href: '/reports/ap',
      items: ['Supplier Ageing Report', 'Supplier Statements', 'Supplier Listing', 'AP Transaction Listing']
    },
    {
      title: 'Inventory Reports',
      description: 'Stock listings, quantity, and valuation reports',
      icon: Package,
      href: '/reports/inventory',
      items: ['Inventory Item Listing', 'Stock Quantity Report', 'Stock Valuation Report']
    },
    {
      title: 'Order Entry Reports',
      description: 'Sales orders, purchase orders, and GRV reports',
      icon: ShoppingCart,
      href: '/reports/oe',
      items: ['Sales Order Listing', 'Purchase Order Listing', 'GRV Listing']
    },
  ];

  return (
    <div className="container mx-auto p-6">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Reports</h1>
        <p className="text-gray-600">Generate comprehensive reports for data analysis and business insights</p>
      </div>
      
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {reportAreas.map((area) => {
          const Icon = area.icon;
          return (
            <Link key={area.title} href={area.href}>
              <Card className="hover:shadow-lg transition-shadow cursor-pointer h-full">
                <CardHeader>
                  <div className="flex items-center space-x-3">
                    <Icon className="h-8 w-8 text-purple-500" />
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
