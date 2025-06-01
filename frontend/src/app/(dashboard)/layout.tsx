'use client';

import { ProtectedRoute } from '@/components/layout/protected-route';
import { useAuth } from '@/contexts/AuthContext';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';
import { 
  Home, 
  Users, 
  Building2, 
  BookOpen, 
  DollarSign, 
  ShoppingCart, 
  Package,
  Settings,
  LogOut,
  Shield,
  Calendar,
  ChevronDown,
  ChevronRight
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface NavItem {
  name: string;
  href: string;
  icon: React.ElementType;
  subItems?: NavItem[];
}

const navigationItems: NavItem[] = [
  { name: 'Dashboard', href: '/dashboard', icon: Home },
  { name: 'Users', href: '/users', icon: Users },
  { name: 'Roles', href: '/roles', icon: Shield },
  { name: 'Companies', href: '/companies', icon: Building2 },
  { name: 'Accounting Periods', href: '/accounting-periods', icon: Calendar },
  {
    name: 'General Ledger',
    href: '/gl',
    icon: BookOpen,
    subItems: [
      { name: 'Chart of Accounts', href: '/gl/accounts', icon: ChevronRight },
      { name: 'Journal Entries', href: '/gl/journal-entries', icon: ChevronRight },
      { name: 'GL Reports', href: '/gl/reports', icon: ChevronRight },
    ],
  },
  { name: 'Accounts Receivable', href: '/ar', icon: DollarSign },
  { name: 'Accounts Payable', href: '/ap', icon: ShoppingCart },
  { name: 'Inventory', href: '/inventory', icon: Package },
  { name: 'Order Entry', href: '/oe', icon: ShoppingCart },
];

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const [openSubMenu, setOpenSubMenu] = useState<string | null>(null);

  const toggleSubMenu = (itemName: string) => {
    setOpenSubMenu(openSubMenu === itemName ? null : itemName);
  };

  return (
    <ProtectedRoute>
      <div className="flex h-screen bg-gray-100">
        {/* Sidebar */}
        <div className="w-64 bg-white shadow-md flex flex-col">
          <div className="p-4 border-b">
            <h1 className="text-2xl font-bold text-gray-800">vinea</h1>
            <p className="text-sm text-gray-600">Core ERP System</p>
          </div>
          
          <nav className="mt-4 flex-grow overflow-y-auto">
            {navigationItems.map((item) => {
              const Icon = item.icon;
              const isActive = pathname === item.href || (item.subItems && item.subItems.some(sub => pathname.startsWith(sub.href)));
              const isSubMenuOpen = openSubMenu === item.name && item.subItems;

              if (item.subItems) {
                return (
                  <div key={item.name}>
                    <button
                      onClick={() => toggleSubMenu(item.name)}
                      className={cn(
                        "flex items-center justify-between w-full px-4 py-2 text-sm font-medium transition-colors",
                        isActive
                          ? "bg-gray-100 text-gray-900 border-l-4 border-primary"
                          : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                      )}
                    >
                      <div className="flex items-center">
                        <Icon className="mr-3 h-5 w-5" />
                        {item.name}
                      </div>
                      {isSubMenuOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                    </button>
                    {isSubMenuOpen && (
                      <div className="pl-4 border-l border-gray-200 ml-2">
                        {item.subItems.map((subItem) => {
                          const SubIcon = subItem.icon;
                          const isSubActive = pathname === subItem.href;
                          return (
                            <Link
                              key={subItem.name}
                              href={subItem.href}
                              className={cn(
                                "flex items-center px-4 py-2 text-sm font-medium transition-colors mt-1",
                                isSubActive
                                  ? "text-primary font-semibold"
                                  : "text-gray-500 hover:text-gray-900"
                              )}
                            >
                              <SubIcon className="mr-3 h-4 w-4" />
                              {subItem.name}
                            </Link>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              }

              return (
                <Link
                  key={item.name}
                  href={item.href}
                  className={cn(
                    "flex items-center px-4 py-2 text-sm font-medium transition-colors",
                    isActive
                      ? "bg-gray-100 text-gray-900 border-l-4 border-primary"
                      : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                  )}
                >
                  <Icon className="mr-3 h-5 w-5" />
                  {item.name}
                </Link>
              );
            })}
          </nav>
          
          <div className="p-4 border-t mt-auto">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-900">
                  {user?.first_name} {user?.last_name}
                </p>
                <p className="text-xs text-gray-500">{user?.username}</p>
              </div>
              <button
                onClick={logout}
                className="p-2 text-gray-400 hover:text-gray-600"
                title="Logout"
              >
                <LogOut className="h-5 w-5" />
              </button>
            </div>
          </div>
        </div>
        
        {/* Main content */}
        <div className="flex-1 overflow-y-auto p-6">
          {children}
        </div>
      </div>
    </ProtectedRoute>
  );
}