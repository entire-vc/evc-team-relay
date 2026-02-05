<script lang="ts">
  import { AlertDialog as AlertDialogPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = AlertDialogPrimitive.TitleProps & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<AlertDialogPrimitive.Title class={cn('text-lg font-semibold', className)} {...restProps}>
  {@render children?.()}
</AlertDialogPrimitive.Title>
